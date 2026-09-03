#!/usr/bin/env python3
"""巴菲特六闸门前选股筛选器（同花顺 API 版）.

仅使用同花顺 fuyao API，不动代码、不动本地数据。
闸门规则：
  ① 便宜:    PE_TTM < 16 且 PB < 3
  ② 好生意:   ROE > 15% 且 扣非ROE > 12%
  ③ 财务稳健: 资产负债率 < 50%
  ④ 利润是真: 经营现金流净额 > 0
  ⑤ 能持续:   3年净利复合增长率 > 0（由 parent_holder_net_profit 计算）
  ⑥ 安全边际: PE 处于10年历史低位（< 30% 分位）※ 个股无此接口，跳过并标注
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

import requests

_API_KEY = (
    os.getenv("HITHINK_FINANCE_API_KEY", "").strip() or os.getenv("FUYAO_API_KEY", "").strip()
)
_BASE = "https://fuyao.aicubes.cn"
_HEADERS = {"X-api-key": _API_KEY}


@dataclass
class StockMetrics:
    ticker: str
    thscode: str
    name: str
    pe_ttm: float | None
    pb: float | None
    roe: float | None
    roe_excl: float | None
    debt_ratio: float | None
    cagr_3y: float | None
    cfo_ok: bool
    gate_fail: str = ""  # 记录在哪个闸门失败


def _safe_float(v) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
        if f != f or abs(f) == float("inf"):
            return None
        return f
    except (ValueError, TypeError):
        return None


# ── 数据获取 ──────────────────────────────────────────────────────────────────


def get_all_stocks() -> list[dict]:
    """分页获取全部 A 股列表."""
    all_stocks: list[dict] = []
    offset = 0
    limit = 5000
    while True:
        resp = requests.get(
            f"{_BASE}/api/meta/tickers/list",
            params={"asset_type": "a-share", "limit": limit, "offset": offset},  # type: ignore[arg-type]
            headers=_HEADERS,
            timeout=15,
        )
        data = resp.json()
        items = data.get("data", {}).get("item", [])
        if not items:
            break
        all_stocks.extend(items)
        offset += len(items)
        if len(items) < limit:
            break
        time.sleep(0.1)
    return all_stocks


def batch_valuations(thscodes: list[str]) -> dict[str, dict]:
    """批量获取估值快照，返回 {thscode: {pe_ttm, pb_mrq, ...}}."""
    result: dict[str, dict] = {}
    for i in range(0, len(thscodes), 100):
        batch = thscodes[i : i + 100]
        try:
            resp = requests.get(
                f"{_BASE}/api/a-share/valuations/snapshot",
                params={"thscodes": ",".join(batch)},  # type: ignore[arg-type]
                headers=_HEADERS,
                timeout=15,
            )
            data = resp.json()
            if data.get("code") == 0:
                for item in data.get("data", {}).get("item", []):
                    result[item["thscode"]] = {
                        "pe_ttm": _safe_float(item.get("pe_ttm")),
                        "pb_mrq": _safe_float(item.get("pb_mrq")),
                        "name": item.get("name", ""),
                    }
        except Exception:
            pass
        time.sleep(0.05)
    return result


def get_financial_indicators(thscode: str, report: str) -> dict[str, float | None]:
    """获取单只股票单期财务指标."""
    result: dict[str, float | None] = {
        "roe": None,
        "roe_excl": None,
        "debt_ratio": None,
    }
    try:
        resp = requests.get(
            f"{_BASE}/api/a-share/financials/indicators",
            params={"thscode": thscode, "report": report},
            headers=_HEADERS,
            timeout=15,
        )
        data = resp.json()
        if data.get("code") != 0:
            return result
        for ab in data.get("data", {}).get("abilities", []):
            for ind in ab.get("indicators", []):
                iid = ind.get("index_id", "")
                val = _safe_float(ind.get("value"))
                if iid == "index_weighted_avg_roe":
                    result["roe"] = val
                elif iid == "index_deduct_weighted_avg_roe":
                    result["roe_excl"] = val
                elif iid == "assets_debt_ratio":
                    result["debt_ratio"] = val
    except Exception:
        pass
    return result


def get_income_statements(thscode: str) -> list[dict]:
    """获取最近4年年报净利润."""
    result: list[dict] = []
    try:
        resp = requests.get(
            f"{_BASE}/api/a-share/financials/income-statements",
            params={"thscode": thscode, "period": "annual", "limit": 4},  # type: ignore[arg-type]
            headers=_HEADERS,
            timeout=15,
        )
        data = resp.json()
        if data.get("code") == 0:
            result = data.get("data", {}).get("item", [])
    except Exception:
        pass
    return result


def get_cash_flow(thscode: str) -> float | None:
    """获取最新一期经营现金流净额（亿元）."""
    try:
        resp = requests.get(
            f"{_BASE}/api/a-share/financials/cash-flow-statements",
            params={"thscode": thscode, "period": "annual", "limit": 1},  # type: ignore[arg-type]
            headers=_HEADERS,
            timeout=15,
        )
        data = resp.json()
        if data.get("code") == 0:
            items = data.get("data", {}).get("item", [])
            if items:
                return _safe_float(items[0].get("act_cash_flow_net"))
    except Exception:
        pass
    return None


# ── 筛选逻辑 ──────────────────────────────────────────────────────────────────


def screen_one(
    thscode: str,
    name: str,
    val: dict,
    fin_latest: dict,
    fin_prev: dict | None,
    income_items: list[dict],
    cfo: float | None,
) -> StockMetrics | None:
    pe = val.get("pe_ttm")
    pb = val.get("pb_mrq")

    # ① 便宜
    if pe is None or pb is None or pe <= 0 or pb <= 0 or pe >= 16 or pb >= 3:
        reason = f"PE={pe} PB={pb}" if pe else "无估值数据"
        return StockMetrics(
            ticker=thscode.replace(".SH", "").replace(".SZ", ""),
            thscode=thscode,
            name=name,
            pe_ttm=pe,
            pb=pb,
            roe=None,
            roe_excl=None,
            debt_ratio=None,
            cagr_3y=None,
            cfo_ok=False,
            gate_fail=f"①{reason}",
        )

    # ② 好生意
    roe = fin_latest.get("roe")
    roe_excl = fin_latest.get("roe_excl")
    if roe is None or roe < 15 or (roe_excl is not None and roe_excl < 12):
        reason = f"ROE={roe} 扣非ROE={roe_excl}"
        return StockMetrics(
            ticker=thscode.replace(".SH", "").replace(".SZ", ""),
            thscode=thscode,
            name=name,
            pe_ttm=pe,
            pb=pb,
            roe=roe,
            roe_excl=roe_excl,
            debt_ratio=None,
            cagr_3y=None,
            cfo_ok=False,
            gate_fail=f"②{reason}",
        )

    # ③ 财务稳健
    debt = fin_latest.get("debt_ratio")
    if debt is None or debt >= 50:
        reason = f"负债率={debt}"
        return StockMetrics(
            ticker=thscode.replace(".SH", "").replace(".SZ", ""),
            thscode=thscode,
            name=name,
            pe_ttm=pe,
            pb=pb,
            roe=roe,
            roe_excl=roe_excl,
            debt_ratio=debt,
            cagr_3y=None,
            cfo_ok=False,
            gate_fail=f"③{reason}",
        )

    # ⑤ 能持续（3年净利复合增长）
    cagr: float | None = None
    if len(income_items) >= 2:
        sorted_items = sorted(income_items, key=lambda x: x.get("fiscal_year", 0))
        profits = [_safe_float(it.get("parent_holder_net_profit")) for it in sorted_items]
        profits = [p for p in profits if p is not None and p > 0]
        if len(profits) >= 2:
            latest = profits[-1]
            earliest = profits[0]
            assert isinstance(latest, float)
            assert isinstance(earliest, float)
            years = max(len(profits) - 1, 1)
            cagr = ((latest / earliest) ** (1 / years) - 1) * 100
    if cagr is None or cagr <= 0:
        return StockMetrics(
            ticker=thscode.replace(".SH", "").replace(".SZ", ""),
            thscode=thscode,
            name=name,
            pe_ttm=pe,
            pb=pb,
            roe=roe,
            roe_excl=roe_excl,
            debt_ratio=debt,
            cagr_3y=cagr,
            cfo_ok=False,
            gate_fail=f"⑤CAGR={cagr}",
        )

    # ④ 利润是真（经营现金流 > 0）
    cfo_ok = cfo is not None and cfo > 0
    if not cfo_ok:
        return StockMetrics(
            ticker=thscode.replace(".SH", "").replace(".SZ", ""),
            thscode=thscode,
            name=name,
            pe_ttm=pe,
            pb=pb,
            roe=roe,
            roe_excl=roe_excl,
            debt_ratio=debt,
            cagr_3y=cagr,
            cfo_ok=False,
            gate_fail=f"④CFO={cfo}",
        )

    # ⑥ 安全边际 — 个股无历史 PE 分位接口，标记为 unknown
    return StockMetrics(
        ticker=thscode.replace(".SH", "").replace(".SZ", ""),
        thscode=thscode,
        name=name,
        pe_ttm=pe,
        pb=pb,
        roe=roe,
        roe_excl=roe_excl,
        debt_ratio=debt,
        cagr_3y=cagr,
        cfo_ok=True,
        gate_fail="⑥无PE历史分位数据",
    )


# ── 主流程 ────────────────────────────────────────────────────────────────────


def main() -> None:

    if not _API_KEY:
        return

    # 1. 获取全量 A 股列表
    stocks = get_all_stocks()
    if not stocks:
        return

    # 过滤 ST / 新股 / 指数
    filtered: list[dict] = []
    for s in stocks:
        name = s.get("name", "")
        code = s.get("ticker", "")
        if any(kw in name for kw in ("ST", "*ST", "N", "C")):
            continue
        if code.startswith(("000", "399")):  # 排除指数
            continue
        filtered.append(s)

    # 2. 批量获取估值快照
    thscodes = [s["thscode"] for s in filtered]
    vals = batch_valuations(thscodes)

    # 3. 逐只筛选
    results_pass: list[StockMetrics] = []
    results_fail: list[StockMetrics] = []
    skipped_no_val: list[str] = []
    len(filtered)

    for i, s in enumerate(filtered):
        thscode = s["thscode"]
        name = s["name"]
        val = vals.get(thscode, {})

        if not val:
            skipped_no_val.append(thscode)
            continue

        # 获取财务指标（最新年报 + 上一年年报）
        fin_latest = get_financial_indicators(thscode, "2025-4")
        fin_prev = get_financial_indicators(thscode, "2024-4")

        # 获取利润表（用于CAGR）
        income_items = get_income_statements(thscode)

        # 获取经营现金流
        cfo = get_cash_flow(thscode)

        m = screen_one(thscode, name, val, fin_latest, fin_prev, income_items, cfo)
        assert m is not None  # screen_one always returns StockMetrics
        if m.gate_fail == "":
            results_pass.append(m)
        else:
            results_fail.append(m)

        if (i + 1) % 500 == 0:
            pass

        time.sleep(0.02)

    # 4. 输出结果

    if results_pass:
        for r in sorted(results_pass, key=lambda x: (x.pe_ttm or 999, -(x.roe or 0))):
            pass
    else:
        pass

    # 闸门分布统计
    fail_gates: dict[str, int] = {}
    for r in results_fail:
        g = r.gate_fail
        if g:
            fail_gates[g] = fail_gates.get(g, 0) + 1

    if fail_gates:
        for _gate, _count in sorted(fail_gates.items(), key=lambda x: -x[1]):
            pass


if __name__ == "__main__":
    main()
