#!/usr/bin/env python3
"""
同花顺巴菲特六闸门筛选器 — 并行加速版（curl 子进程，彻底避免连接池卡死）
闸门规则：
  ① 便宜：PE_TTM < 16 且 PB < 3
  ② 好生意：ROE > 15% 且 扣非ROE > 12%
  ③ 财务稳健：资产负债率 < 50%
  ④ 利润是真：经营现金流净额 > 0
  ⑤ 能持续：3年净利CAGR > 0
  ⑥ 安全边际：个股无 PE 历史分位接口，标注为未知
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from urllib.parse import urlencode

from trade_krono_cli.utils import add_ticker_prefix

_API_KEY = (
    os.getenv("HITHINK_FINANCE_API_KEY", "").strip() or os.getenv("FUYAO_API_KEY", "").strip()
)
_BASE = "https://fuyao.aicubes.cn"
_CONCURRENCY = 8


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


def _api_get(path: str, params: dict | None = None, timeout: int = 8) -> dict | None:
    """用 curl 子进程发 GET 请求，每请求独立进程，彻底避免连接池问题。"""
    url = f"{_BASE}{path}"
    if params:
        url += "?" + urlencode(params)
    cmd = [
        "curl",
        "-s",
        "--max-time",
        str(timeout),
        "-w",
        "\n%{http_code}",
        url,
        "-H",
        f"X-api-key: {_API_KEY}",
        "-A",
        "BuffettScreen/1.0",
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 2)
        stdout = r.stdout.strip()
        last_nl = stdout.rfind("\n")
        if last_nl >= 0:
            body = stdout[:last_nl]
            http_code = int(stdout[last_nl + 1 :])
        else:
            return None
        if http_code != 200:
            return None
        return json.loads(body)
    except Exception:
        return None


# ── 数据获取 ─────────────────────────────────────────────────────────────────


def get_all_stocks() -> list[dict]:
    """分页获取全部 A 股列表"""
    all_stocks: list[dict] = []
    offset = 0
    limit = 5000
    while True:
        data = _api_get(
            "/api/meta/tickers/list",
            {"asset_type": "a-share", "limit": limit, "offset": offset},
            timeout=30,
        )
        if not data:
            break
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
    """批量获取估值快照（每批50只，顺序执行避免过多并发）"""
    result: dict[str, dict] = {}
    total_batches = (len(thscodes) + 49) // 50
    for idx, i in enumerate(range(0, len(thscodes), 50)):
        batch = thscodes[i : i + 50]
        data = _api_get(
            "/api/a-share/valuations/snapshot", {"thscodes": ",".join(batch)}, timeout=15
        )
        if data and data.get("code") == 0:
            for item in data.get("data", {}).get("item", []):
                result[item["thscode"]] = {
                    "pe_ttm": _safe_float(item.get("pe_ttm")),
                    "pb_mrq": _safe_float(item.get("pb_mrq")),
                    "name": item.get("name", ""),
                }
        if (idx + 1) % 10 == 0:
            print(f"   估值批次进度: {idx + 1}/{total_batches} ({len(result)} 只)", flush=True)
        time.sleep(0.02)
    return result


def _fetch_financials(thscode: str, report: str) -> dict[str, float | None]:
    """获取单只股票单期财务指标"""
    result: dict[str, float | None] = {"roe": None, "roe_excl": None, "debt_ratio": None}
    data = _api_get(
        "/api/a-share/financials/indicators", {"thscode": thscode, "report": report}, timeout=8
    )
    if not data or data.get("code") != 0:
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
    return result


def _fetch_income(thscode: str) -> list[dict]:
    """获取最近4年年报净利润"""
    data = _api_get(
        "/api/a-share/financials/income-statements",
        {"thscode": thscode, "period": "annual", "limit": 4},
        timeout=8,
    )
    if not data or data.get("code") != 0:
        return []
    return data.get("data", {}).get("item", [])


def _fetch_cfo(thscode: str) -> Optional[float]:
    """获取最新一期经营现金流净额"""
    data = _api_get(
        "/api/a-share/financials/cash-flow-statements",
        {"thscode": thscode, "period": "annual", "limit": 1},
        timeout=8,
    )
    if not data or data.get("code") != 0:
        return None
    items = data.get("data", {}).get("item", [])
    if items:
        return _safe_float(items[0].get("act_cash_flow_net"))
    return None


# ── 筛选逻辑 ─────────────────────────────────────────────────────────────────


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
            gate_fail=f"②ROE={roe} 扣非ROE={roe_excl}",
        )

    # ③ 财务稳健
    debt = fin_latest.get("debt_ratio")
    if debt is None or debt >= 50:
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
            gate_fail=f"③负债率={debt}",
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
    print(
        f"🔍 巴菲特六闸门筛选（同花顺 API 版·curl并行）— {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )
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
    print("   ✅ 估值数据获取完成\n", flush=True)

    # 3. 并行筛选
    print(f"🔎 逐只执行六闸门筛选（并发={_CONCURRENCY}）...\n")
    results_pass: list[StockMetrics] = []
    results_fail: list[StockMetrics] = []
    skipped_no_val: list[str] = []
    total = len(filtered)
    start_time = time.time()

    def worker(s: dict) -> StockMetrics:
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
                if m.ticker:
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

    # 4. 写入结果文件
    date_str = datetime.now().strftime("%Y%m%d")
    out_path = f"outputs/results/buffett_screen_{date_str}.txt"
    os.makedirs("outputs/results", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"巴菲特六闸门筛选结果 — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
        f.write(f"通过五闸门（①~⑤）的股票共 {len(results_pass)} 只\n\n")
        f.write(
            f"  {'代码':<8} {'名称':<10} {'PE_TTM':>7} {'PB':>6} {'ROE%':>7} "
            f"{'扣非ROE%':>8} {'负债率%':>7} {'CAGR%':>7}\n"
        )
        f.write(
            f"  {'-' * 8} {'-' * 10} {'-' * 7} {'-' * 6} {'-' * 7} {'-' * 8} {'-' * 7} {'-' * 7}\n"
        )
        for r in sorted(results_pass, key=lambda x: (x.pe_ttm or 999, -(x.roe or 0))):
            f.write(
                f"  {r.ticker:<8} {r.name:<10} {r.pe_ttm:>7.1f} {r.pb:>6.2f} "
                f"{r.roe:>7.1f} {r.roe_excl:>8.1f} {r.debt_ratio:>7.1f} {r.cagr_3y:>7.1f}\n"
            )
        f.write(f"\n失败分布（共 {len(results_fail)} 只）：\n")
        for gate, count in sorted(fail_gates.items(), key=lambda x: -x[1]):
            f.write(f"  {gate}: {count} 只\n")
        f.write("\n注：闸门⑥（PE历史分位）同花顺 API 仅支持指数，个股无法验证。\n")

    print(f"\n  💾 结果已保存至: {out_path}")

    # 5. 输出股票代码列表（供后续流水线使用）
    ticker_list = [add_ticker_prefix(r.ticker) for r in results_pass]
    if ticker_list:
        print(f"\n  📋 通过筛选的股票代码: {','.join(ticker_list)}")


if __name__ == "__main__":
    main()
