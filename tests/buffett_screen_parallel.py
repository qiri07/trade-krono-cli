#!/usr/bin/env python3
"""
同花顺巴菲特六闸门筛选器 — 并行加速版
原脚本 tests/buffett_screen.py 不变，此脚本仅用于并行执行筛选逻辑。
闸门规则不变，结果格式与原版一致。
"""

from __future__ import annotations

import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import requests

_API_KEY = (
    os.getenv("HITHINK_FINANCE_API_KEY", "").strip() or os.getenv("FUYAO_API_KEY", "").strip()
)
_BASE = "https://fuyao.aicubes.cn"
_HEADERS = {"X-api-key": _API_KEY}
_CONCURRENCY = 20  # 并发数，避免被限流


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
    gate_fail: str = ""


def _safe_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
        if f != f or abs(f) == float("inf"):
            return None
        return f
    except (ValueError, TypeError):
        return None


# ── 数据获取（与原脚本相同，支持并发）─────────────────────────────────────────


def get_all_stocks() -> list[dict]:
    """分页获取全部 A 股列表"""
    all_stocks: list[dict] = []
    offset = 0
    limit = 5000
    while True:
        resp = requests.get(
            f"{_BASE}/api/meta/tickers/list",
            params={"asset_type": "a-share", "limit": limit, "offset": offset},  # type: ignore[arg-type]
            headers=_HEADERS,
            timeout=30,
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
    """批量获取估值快照"""
    result: dict[str, dict] = {}
    for i in range(0, len(thscodes), 100):
        batch = thscodes[i : i + 100]
        try:
            resp = requests.get(
                f"{_BASE}/api/a-share/valuations/snapshot",
                params={"thscodes": ",".join(batch)},
                headers=_HEADERS,
                timeout=30,
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


def _fetch_financials(thscode: str, report: str) -> dict[str, float | None]:
    """获取单只股票单期财务指标"""
    result: dict[str, float | None] = {"roe": None, "roe_excl": None, "debt_ratio": None}
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


def _fetch_income(thscode: str) -> list[dict]:
    """获取最近4年年报净利润"""
    try:
        resp = requests.get(
            f"{_BASE}/api/a-share/financials/income-statements",
            params={"thscode": thscode, "period": "annual", "limit": 4},  # type: ignore[arg-type]
            headers=_HEADERS,
            timeout=15,
        )
        data = resp.json()
        if data.get("code") == 0:
            return data.get("data", {}).get("item", [])
    except Exception:
        pass
    return []


def _fetch_cfo(thscode: str) -> Optional[float]:
    """获取最新一期经营现金流净额"""
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


# ── 筛选逻辑（与原脚本完全一致）───────────────────────────────────────────────


def screen_one(
    thscode: str,
    name: str,
    val: dict,
    fin_latest: dict,
    fin_prev: dict | None,
    income_items: list[dict],
    cfo: float | None,
) -> StockMetrics:
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
            latest, earliest = profits[-1], profits[0]
            assert isinstance(latest, float) and isinstance(earliest, float)
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

    # ⑥ 安全边际 — 个股无历史 PE 分位接口
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


def process_one_stock(s: dict, vals: dict) -> StockMetrics:
    """处理单只股票：获取财务数据 + 筛选"""
    thscode = s["thscode"]
    name = s["name"]
    val = vals.get(thscode, {})

    if not val:
        return StockMetrics(
            ticker="",
            thscode=thscode,
            name=name,
            pe_ttm=None,
            pb=None,
            roe=None,
            roe_excl=None,
            debt_ratio=None,
            cagr_3y=None,
            cfo_ok=False,
            gate_fail="无估值数据",
        )

    fin_latest = _fetch_financials(thscode, "2025-4")
    fin_prev = _fetch_financials(thscode, "2024-4")
    income_items = _fetch_income(thscode)
    cfo = _fetch_cfo(thscode)

    return screen_one(thscode, name, val, fin_latest, fin_prev, income_items, cfo)


# ── 主流程 ────────────────────────────────────────────────────────────────────


def main() -> None:
    print(f"🔍 巴菲特六闸门筛选（同花顺 API 版·并行）— {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 85)

    if not _API_KEY:
        print("❌ 未找到 HITHINK_FINANCE_API_KEY / FUYAO_API_KEY")
        sys.exit(1)

    # 1. 获取全量 A 股列表
    print("📋 获取 A 股列表...")
    stocks = get_all_stocks()
    if not stocks:
        print("❌ 无法获取股票列表")
        sys.exit(1)

    filtered: list[dict] = []
    for s in stocks:
        name = s.get("name", "")
        code = s.get("ticker", "")
        if any(kw in name for kw in ("ST", "*ST", "N", "C")):
            continue
        if code.startswith(("000", "399")):
            continue
        filtered.append(s)

    print(f"   共 {len(stocks)} 只，过滤后 {len(filtered)} 只（排除ST/新股/指数）\n")

    # 2. 批量获取估值快照
    thscodes = [s["thscode"] for s in filtered]
    print("📊 批量获取估值快照（PE/PB）...")
    vals = batch_valuations(thscodes)
    print(f"   获取到 {len(vals)} 只估值数据\n")

    # 3. 并行筛选
    print(f"🔎 逐只执行六闸门筛选（并发={_CONCURRENCY}）...\n")
    results_pass: list[StockMetrics] = []
    results_fail: list[StockMetrics] = []
    skipped_no_val: list[str] = []
    total = len(filtered)
    start_time = time.time()

    def worker(s: dict):
        return process_one_stock(s, vals)

    with ThreadPoolExecutor(max_workers=_CONCURRENCY) as executor:
        futures = {executor.submit(worker, s): s for s in filtered}
        done_count = 0

        for future in as_completed(futures):
            done_count += 1
            try:
                m = future.result()
            except Exception as e:
                s = futures[future]
                print(f"  ❌ {s.get('thscode', '?')}: 处理异常 {e}")
                continue

            if m.gate_fail == "" or (m.gate_fail and m.gate_fail.startswith("⑥")):
                if m.ticker:  # 真正通过五闸门
                    results_pass.append(m)
                    print(
                        f"  ✅ {m.ticker} {m.name}: PE={m.pe_ttm:.1f} PB={m.pb:.2f} "
                        f"ROE={m.roe:.1f}% 扣非ROE={m.roe_excl:.1f}% "
                        f"负债率={m.debt_ratio:.1f}% CAGR={m.cagr_3y:.1f}%"
                    )
                else:
                    skipped_no_val.append(m.thscode)
            else:
                results_fail.append(m)

            if done_count % 500 == 0:
                elapsed = time.time() - start_time
                rate = done_count / elapsed if elapsed > 0 else 0
                eta = (total - done_count) / rate if rate > 0 else 0
                print(
                    f"  进度: {done_count}/{total} ({done_count * 100 // total}%) "
                    f"{'=' * (done_count * 30 // total)}{' ' * (30 - done_count * 30 // total)} "
                    f"通过:{len(results_pass)} 失败:{len(results_fail)} "
                    f"速度:{rate:.1f}只/秒 ETA:{eta / 60:.1f}分钟"
                )

    elapsed = time.time() - start_time
    print(f"\n{'=' * 85}")
    print(
        f"  巴菲特六闸门筛选结果 — {datetime.now().strftime('%Y-%m-%d %H:%M')} | 耗时: {elapsed / 60:.1f}分钟"
    )
    print(f"{'=' * 85}")
    print("  ⚠️  注：闸门⑥（PE历史分位）同花顺 API 仅支持指数，个股无法验证，已标注")

    if results_pass:
        print(f"\n  ✅ 通过五闸门（①~⑤）的股票共 {len(results_pass)} 只：\n")
        print(
            f"  {'代码':<8} {'名称':<10} {'PE_TTM':>7} {'PB':>6} {'ROE%':>7} "
            f"{'扣非ROE%':>8} {'负债率%':>7} {'CAGR%':>7}"
        )
        print(f"  {'-' * 8} {'-' * 10} {'-' * 7} {'-' * 6} {'-' * 7} {'-' * 8} {'-' * 7} {'-' * 7}")
        for r in sorted(results_pass, key=lambda x: (x.pe_ttm or 999, -(x.roe or 0))):
            print(
                f"  {r.ticker:<8} {r.name:<10} {r.pe_ttm:>7.1f} {r.pb:>6.2f} "
                f"{r.roe:>7.1f} {r.roe_excl:>8.1f} {r.debt_ratio:>7.1f} {r.cagr_3y:>7.1f}"
            )
    else:
        print("\n  没有股票通过全部五闸门前筛")

    fail_gates: dict[str, int] = {}
    for r in results_fail:
        g = r.gate_fail
        if g:
            fail_gates[g] = fail_gates.get(g, 0) + 1

    if fail_gates:
        print(f"\n  📊 失败分布（共 {len(results_fail)} 只）：")
        for gate, count in sorted(fail_gates.items(), key=lambda x: -x[1]):
            print(f"    {gate}: {count} 只")

    print(f"\n  无估值数据跳过: {len(skipped_no_val)} 只")
    print(f"  总计: {len(filtered)} 只 | 通过五闸门: {len(results_pass)} 只")
    print(f"{'=' * 85}")


if __name__ == "__main__":
    main()
