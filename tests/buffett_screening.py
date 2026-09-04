"""buffett_screening — 巴菲特六闸门筛选业务逻辑。

包含：API 客户端、数据获取、StockMetrics 数据模型、五闸门筛选、
三项深度验证（ROE历史、CFO比率、CAGR确认）、盈利稳定性/现金流质量评估。
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

from tests.buffett_cache import (
    TTL_STOCKS,
    cache_get_list,
    cache_set_list,
)
from trade_krono_cli.utils import add_ticker_prefix

_API_KEY = (
    os.getenv("HITHINK_FINANCE_API_KEY", "").strip() or os.getenv("FUYAO_API_KEY", "").strip()
)
_BASE = "https://fuyao.aicubes.cn"
_CONCURRENCY = 8


def _safe_float(v: object) -> float | None:
    """安全地将任意值转为 float，失败返回 None。"""
    if v is None:
        return None
    try:
        f = float(str(v))
        if f != f or abs(f) == float("inf"):
            return None
        return f
    except (ValueError, TypeError):
        return None


# ── API 客户端 ──────────────────────────────────────────────────────────────────


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


# ── 数据获取 ────────────────────────────────────────────────────────────────────


def get_all_stocks(conn) -> list[dict]:
    """分页获取全部 A 股列表（带缓存）。"""
    cached = cache_get_list(conn, "all_a_share")
    if cached is not None:
        return cached

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

    cache_set_list(conn, "all_a_share", all_stocks, TTL_STOCKS)
    return all_stocks


def batch_valuations(
    thscodes: list[str], conn, cache: dict | None = None
) -> tuple[dict[str, dict], dict[str, dict]]:
    """批量获取估值快照（带缓存，返回 (结果, 新缓存条目)）。"""
    result: dict[str, dict] = {}
    new_entries: dict[str, dict] = {}

    for idx, i in enumerate(range(0, len(thscodes), 50)):
        batch = thscodes[i : i + 50]
        batch_key = "val_batch_" + ",".join(sorted(batch))

        # 先查缓存
        cached = cache.get(batch_key) if cache else None
        if cached is not None:
            result.update(cached)
            continue

        # 缓存未命中，请求 API
        data = _api_get(
            "/api/a-share/valuations/snapshot",
            {"thscodes": ",".join(batch)},
            timeout=15,
        )
        batch_result: dict[str, dict] = {}
        if data and data.get("code") == 0:
            for item in data.get("data", {}).get("item", []):
                thscode = item["thscode"]
                entry = {
                    "pe_ttm": _safe_float(item.get("pe_ttm")),
                    "pb_mrq": _safe_float(item.get("pb_mrq")),
                    "name": item.get("name", ""),
                }
                batch_result[thscode] = entry
                new_entries[f"val_{thscode}"] = entry

            result.update(batch_result)
            new_entries[batch_key] = batch_result

        if (idx + 1) % 10 == 0:
            pass
        time.sleep(0.02)
    return result, new_entries


def _fetch_financials(
    thscode: str, report: str, conn, cache: dict | None = None
) -> tuple[dict[str, float | None], dict | None]:
    """获取单只股票单期财务指标（带缓存，返回 (结果, 新缓存条目)）。"""
    cache_key = f"fin_{thscode}_{report}"
    cached = cache.get(cache_key) if cache else None
    if cached is not None:
        return cached, None

    result: dict[str, float | None] = {"roe": None, "roe_excl": None, "debt_ratio": None}
    data = _api_get(
        "/api/a-share/financials/indicators",
        {"thscode": thscode, "report": report},
        timeout=8,
    )
    if not data or data.get("code") != 0:
        return result, None
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

    return result, {cache_key: result}


def _fetch_income(thscode: str, conn, cache: dict | None = None) -> tuple[list[dict], dict | None]:
    """获取最近4年年报净利润（带缓存，返回 (结果, 新缓存条目)）。"""
    cache_key = f"income_{thscode}"
    cached = cache.get(cache_key) if cache else None
    if cached is not None:
        return cached, None

    data = _api_get(
        "/api/a-share/financials/income-statements",
        {"thscode": thscode, "period": "annual", "limit": 4},
        timeout=8,
    )
    items: list[dict] = []
    new_entry: dict | None = None
    if data and data.get("code") == 0:
        items = data.get("data", {}).get("item", [])
        new_entry = {cache_key: {"items": items}}
    return items, new_entry


def _fetch_cfo(thscode: str, conn, cache: dict | None = None) -> tuple[float | None, dict | None]:
    """获取最新一期经营现金流净额（带缓存，返回 (结果, 新缓存条目)）。"""
    cache_key = f"cfo_{thscode}"
    cached = cache.get(cache_key) if cache else None
    if cached is not None:
        return cached, None

    data = _api_get(
        "/api/a-share/financials/cash-flow-statements",
        {"thscode": thscode, "period": "annual", "limit": 1},
        timeout=8,
    )
    cfo: float | None = None
    new_entry: dict | None = None
    if data and data.get("code") == 0:
        items = data.get("data", {}).get("item", [])
        if items:
            cfo = _safe_float(items[0].get("act_cash_flow_net"))
        new_entry = {cache_key: {"cfo": cfo}}
    return cfo, new_entry


# ── 三项关键验证 ────────────────────────────────────────────────────────────────


def fetch_roe_history(
    thscode: str, conn, cache: dict | None = None
) -> tuple[list[dict], dict | None]:
    """拉取近10年ROE序列（验证持续盈利能力）。"""
    cache_key = f"roe_hist_{thscode}"
    cached = cache.get(cache_key) if cache else None
    if cached is not None:
        return cached, None

    history: list[dict] = []
    years = list(range(2016, 2026))  # 2016-2025

    for year in years:
        report = f"{year}-4"  # 年报
        data = _api_get(
            "/api/a-share/financials/indicators",
            {"thscode": thscode, "report": report},
            timeout=8,
        )
        if not data or data.get("code") != 0:
            continue
        roe = None
        roe_excl = None
        for ab in data.get("data", {}).get("abilities", []):
            for ind in ab.get("indicators", []):
                if ind["index_id"] == "index_weighted_avg_roe":
                    roe = _safe_float(ind.get("value"))
                elif ind["index_id"] == "index_deduct_weighted_avg_roe":
                    roe_excl = _safe_float(ind.get("value"))
        if roe is not None:
            history.append({"year": year, "roe": roe, "roe_excl": roe_excl})

    if history:
        new_entry = {cache_key: history}
    else:
        new_entry = None
    return history, new_entry


def fetch_cfo_ratio_history(
    thscode: str, conn, cache: dict | None = None
) -> tuple[list[dict], dict | None]:
    """计算近5年经营现金流/净利润比率（验证利润质量）。"""
    cache_key = f"cfo_ratio_{thscode}"
    cached = cache.get(cache_key) if cache else None
    if cached is not None:
        return cached, None

    ratio_history: list[dict] = []
    years = list(range(2021, 2026))  # 2021-2025

    for year in years:
        # 获取净利润
        income_data = _api_get(
            "/api/a-share/financials/income-statements",
            {"thscode": thscode, "period": "annual", "limit": 1, "fiscal_year": year},
            timeout=8,
        )
        net_profit = None
        if income_data and income_data.get("code") == 0:
            items = income_data.get("data", {}).get("item", [])
            if items:
                net_profit = _safe_float(items[0].get("parent_holder_net_profit"))

        # 获取经营现金流
        cfo_data = _api_get(
            "/api/a-share/financials/cash-flow-statements",
            {"thscode": thscode, "period": "annual", "limit": 1, "fiscal_year": year},
            timeout=8,
        )
        cfo = None
        if cfo_data and cfo_data.get("code") == 0:
            items = cfo_data.get("data", {}).get("item", [])
            if items:
                cfo = _safe_float(items[0].get("act_cash_flow_net"))

        if net_profit and net_profit > 0 and cfo is not None:
            ratio = cfo / net_profit
            ratio_history.append(
                {"year": year, "cfo": cfo, "net_profit": net_profit, "ratio": ratio}
            )

    if ratio_history:
        new_entry = {cache_key: ratio_history}
    else:
        new_entry = None
    return ratio_history, new_entry


def evaluate_profitability_stability(roe_history: list[dict]) -> str:
    """评估盈利稳定性（基于ROE连续性）。

    评级标准：
      - 卓越：≥80%年份 ROE≥15% 且标准差 < 5
      - 优秀：≥60%年份 ROE≥15% 且标准差 < 8
      - 良好：≥50%年份 ROE≥15%
      - 一般：≥40%年份 ROE≥15%
      - 较弱：< 40%年份 ROE≥15%
    """
    if not roe_history or len(roe_history) < 5:
        return "数据不足"

    qualified_years = sum(1 for r in roe_history if r.get("roe") and r["roe"] >= 15)
    ratio = qualified_years / len(roe_history)

    roe_values = [r["roe"] for r in roe_history if r.get("roe")]
    if len(roe_values) >= 2:
        avg_roe = sum(roe_values) / len(roe_values)
        variance = sum((x - avg_roe) ** 2 for x in roe_values) / len(roe_values)
        std_dev = variance**0.5
    else:
        std_dev = 0

    if ratio >= 0.8 and std_dev < 5:
        return "卓越"
    elif ratio >= 0.6 and std_dev < 8:
        return "优秀"
    elif ratio >= 0.5:
        return "良好"
    elif ratio >= 0.4:
        return "一般"
    return "较弱"


def evaluate_cash_quality(cfo_ratio_history: list[dict]) -> str:
    """评估利润质量（基于现金流覆盖率）。

    评级标准：
      - 优质：≥80%年份 ratio≥0.8 且平均 ratio ≥ 0.9
      - 良好：≥60%年份 ratio≥0.8 且平均 ratio ≥ 0.7
      - 一般：平均 ratio ≥ 0.5
      - 较差：平均 ratio < 0.5
    """
    if not cfo_ratio_history:
        return "数据不足"

    good_years = sum(1 for r in cfo_ratio_history if r.get("ratio") and r["ratio"] >= 0.8)
    ratio = good_years / len(cfo_ratio_history)
    avg_ratio = sum(r.get("ratio", 0) for r in cfo_ratio_history) / len(cfo_ratio_history)

    if ratio >= 0.8 and avg_ratio >= 0.9:
        return "优质"
    elif ratio >= 0.6 and avg_ratio >= 0.7:
        return "良好"
    elif avg_ratio >= 0.5:
        return "一般"
    return "较差"


# ── 筛选逻辑 ────────────────────────────────────────────────────────────────────


@dataclass
class StockMetrics:
    """单只股票的筛选指标，包含五闸门结果及三项深度验证数据。"""

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

    # 新增验证字段
    roe_10y: list[dict] | None = None  # 近10年ROE序列
    cfo_ratio_5y: list[dict] | None = None  # 近5年现金流/净利润比率
    cagr_is_net_profit: bool = True  # CAGR是否为净利润口径
    profitability_stability: str = ""  # 盈利稳定性评级
    cash_quality_rating: str = ""  # 利润质量评级


def screen_one(
    thscode: str,
    name: str,
    val: dict,
    fin_latest: dict,
    fin_prev: dict | None,
    income_items: list[dict],
    cfo: float | None,
) -> StockMetrics:
    """五闸门筛选核心逻辑。通过返回 gate_fail="" 的空失败标记，失败则返回具体原因。"""
    pe = val.get("pe_ttm")
    pb = val.get("pb_mrq")

    # ① 便宜：PE_TTM < 16 且 PB < 3
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

    # ② 好生意：ROE > 15% 且 扣非ROE > 12%
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

    # ③ 财务稳健：资产负债率 < 50%
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

    # ⑤ 能持续：3年净利复合增长率 > 0
    cagr: float | None = None
    if len(income_items) >= 2:
        sorted_items = sorted(income_items, key=lambda x: x.get("fiscal_year", 0))
        profits = [_safe_float(it.get("parent_holder_net_profit")) for it in sorted_items]
        profits = [p for p in profits if p is not None and p > 0]
        if len(profits) >= 2:
            latest, earliest = profits[-1], profits[0]
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

    # ④ 利润是真：经营现金流 > 0
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

    # ⑥ 安全边际 — 个股无历史 PE 分位接口，标注为未知
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


def process_one_stock(s: dict, vals: dict, conn, cache: dict) -> StockMetrics:
    """处理单只股票：初步筛选 + 通过后再做三项验证。"""
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

    # 先获取必要财务数据做初步筛选
    fin_latest, _ = _fetch_financials(thscode, "2025-4", conn, cache)
    fin_prev, _ = _fetch_financials(thscode, "2024-4", conn, cache)
    income_items, _ = _fetch_income(thscode, conn, cache)
    cfo, _ = _fetch_cfo(thscode, conn, cache)

    # 做初步筛选（五闸门）
    metrics = screen_one(thscode, name, val, fin_latest, fin_prev, income_items, cfo)

    # 仅对通过初步筛选的股票做三项深度验证
    if metrics.cfo_ok or (metrics.gate_fail and metrics.gate_fail.startswith("⑥")):
        roe_history, _ = fetch_roe_history(thscode, conn, cache)
        cfo_ratio_history, _ = fetch_cfo_ratio_history(thscode, conn, cache)
        metrics.roe_10y = roe_history
        metrics.cfo_ratio_5y = cfo_ratio_history
        metrics.cagr_is_net_profit = True
        metrics.profitability_stability = evaluate_profitability_stability(roe_history)
        metrics.cash_quality_rating = evaluate_cash_quality(cfo_ratio_history)

    return metrics


def run_screening(
    filtered: list[dict],
    vals: dict,
    conn,
    cache: dict,
    concurrency: int = _CONCURRENCY,
) -> tuple[list[StockMetrics], list[StockMetrics], list[str], float]:
    """并行执行筛选，返回 (通过列表, 失败列表, 跳过列表, 耗时秒数)。"""
    results_pass: list[StockMetrics] = []
    results_fail: list[StockMetrics] = []
    skipped_no_val: list[str] = []
    total = len(filtered)
    start_time = time.time()

    def worker(s: dict) -> StockMetrics:
        return process_one_stock(s, vals, conn, cache)

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {executor.submit(worker, s): s for s in filtered}
        done_count = 0
        for future in as_completed(futures):
            done_count += 1
            try:
                m = future.result()
            except Exception:
                continue
            if m.gate_fail == "" or (m.gate_fail and m.gate_fail.startswith("⑥")):
                if m.ticker:
                    results_pass.append(m)
                else:
                    skipped_no_val.append(m.thscode)
            else:
                results_fail.append(m)
            if done_count % 500 == 0:
                elapsed = time.time() - start_time
                rate = done_count / elapsed if elapsed > 0 else 0
                remaining = (total - done_count) / rate if rate > 0 else 0
                print(
                    f"  进度 {done_count}/{total} ({done_count * 100 // total}%)，"
                    f"预计剩余 {remaining:.0f}s",
                    flush=True,
                )

    elapsed = time.time() - start_time
    print(f"  筛选完成（{elapsed:.1f}s）", flush=True)
    return results_pass, results_fail, skipped_no_val, elapsed


def write_result_file(
    out_path: str, results_pass: list[StockMetrics], results_fail: list[StockMetrics]
) -> None:
    """将筛选结果写入文本文件。"""
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"巴菲特六闸门筛选结果 — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
        f.write(f"通过五闸门（①~⑤）的股票共 {len(results_pass)} 只\n\n")
        f.write(
            f"  {'代码':<8} {'名称':<10} {'PE_TTM':>7} {'PB':>6} {'ROE%':>7} "
            f"{'扣非ROE%':>8} {'负债率%':>7} {'CAGR%':>7}  {'稳定性':<6} {'现金流质量'}\n",
        )
        f.write(
            f"  {'-' * 8} {'-' * 10} {'-' * 7} {'-' * 6} {'-' * 7} {'-' * 8} "
            f"{'-' * 7} {'-' * 7}  {'-' * 8} {'-' * 10}\n",
        )
        for r in sorted(results_pass, key=lambda x: (x.pe_ttm or 999, -(x.roe or 0))):
            stability = r.profitability_stability or "-"
            cash_qual = r.cash_quality_rating or "-"
            f.write(
                f"  {r.ticker:<8} {r.name:<10} {r.pe_ttm:>7.1f} {r.pb:>6.2f} "
                f"{r.roe:>7.1f} {r.roe_excl:>8.1f} {r.debt_ratio:>7.1f} {r.cagr_3y:>7.1f}"
                f"  {stability:<6} {cash_qual}\n",
            )
        f.write(f"\n失败分布（共 {len(results_fail)} 只）：\n")
        fail_gates: dict[str, int] = {}
        for r in results_fail:
            g = r.gate_fail
            if g:
                fail_gates[g] = fail_gates.get(g, 0) + 1
        for gate, count in sorted(fail_gates.items(), key=lambda x: -x[1]):
            f.write(f"  {gate}: {count} 只\n")
        f.write("\n注：闸门⑥（PE历史分位）同花顺 API 仅支持指数，个股无法验证。\n")
    print(f"结果已写入：{out_path}", flush=True)


def build_ticker_list(results_pass: list[StockMetrics]) -> list[str]:
    """构建供流水线使用的股票代码列表（带交易所后缀）。"""
    return [add_ticker_prefix(r.ticker) for r in results_pass]
