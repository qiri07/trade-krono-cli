"""six_dim 选股 — 主流程。

负责加载股票池、执行六维筛选与排序，返回推荐候选列表。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

from loguru import logger

from scripts._utils import (
    get_cache_db_path,
    load_metadata_index,
    normalize_ticker_key,
    ticker_to_thscode,
)
from scripts.six_dim.constants import (
    DIM_WEIGHTS,
    MAX_PB,
    MAX_PE,
    MIN_MARKET_CAP_YI,
)
from scripts.six_dim.holder import fetch_holder_counts, score_holder
from scripts.six_dim.logic import load_fundamentals, score_logic
from scripts.six_dim.market_cap import fetch_spot_snapshot
from scripts.six_dim.models import StockScore
from scripts.six_dim.sorting import sort_scores
from scripts.six_dim.technical import (
    load_technicals,
    score_position,
    score_vol_price,
    trend_gate_and_score,
)
from scripts.six_dim.valuation import fetch_valuation_history, score_valuation


def _load_universe(meta: dict[str, dict[str, object]]) -> dict[str, str]:
    """从元数据加载股票池（排除 ST/北交所/港股/ETF）。

    Args:
        meta: 归一化后的元数据字典。

    Returns:
        6 位代码 -> 带前缀 ticker 的映射。
    """
    all_tickers = set(meta.keys())
    universe: dict[str, str] = {}
    for ticker in all_tickers:
        bare = ticker.split(".")[-1] if "." in ticker else ticker
        if len(bare) != 6 or not bare.isdigit():
            continue
        # 过滤 ST、北交所、港股、ETF
        val = meta.get(ticker)
        name = str(val.get("name", "")) if isinstance(val, dict) else ""
        if any(kw in name for kw in ("ST", "*ST", "退")):
            continue
        if bare.startswith(("920", "43", "83", "87")):
            continue
        if bare.startswith("5") or bare[:3] in ("159", "156", "158"):
            continue
        normalized = normalize_ticker_key(ticker)
        if normalized and not normalized.startswith(("bj.", "hk.")):
            universe[bare] = normalized
    return universe


def _load_pe_pb(buffett_db: Path) -> dict[str, tuple[float | None, float | None]]:
    """从巴菲特缓存加载 PE_TTM / PB_MRQ。

    Args:
        buffett_db: ``buffett_cache.db`` 路径。

    Returns:
        ``6 位代码 -> (PE, PB)``。
    """
    if not buffett_db.exists():
        return {}
    conn = sqlite3.connect(f"file:{buffett_db}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT key, data FROM cache_valuations").fetchall()
    finally:
        conn.close()
    out: dict[str, tuple[float | None, float | None]] = {}
    for key, data in rows:
        if not key.startswith("val_"):
            continue
        code = key[4:].split(".")[0]
        try:
            v = json.loads(data)
        except json.JSONDecodeError:
            continue
        pe = v.get("pe_ttm")
        pb = v.get("pb_mrq")
        out[code] = (
            float(pe) if isinstance(pe, (int, float)) else None,
            float(pb) if isinstance(pb, (int, float)) else None,
        )
    return out


def screen(
    *,
    date_str: str | None = None,
    min_cap: float = MIN_MARKET_CAP_YI,
    fetch: bool = True,
    max_candidates: int = 800,
) -> tuple[list[StockScore], dict[str, int]]:
    """执行六维筛选与排序。

    Args:
        date_str: 分析日期（YYYY-MM-DD），缺省为最近交易日。
        min_cap: 最小总市值（亿元）。
        fetch: 是否联网拉取估值分位/股东户数/快照（False 时仅用本地缓存）。
        max_candidates: 进入技术面计算的最大候选数（按逻辑分排序截断）。

    Returns:
        ``(按分档字典序排序的候选列表, 各关卡人数统计)``；列表包含全部通过门槛的股票，
        由调用方按 ``--top`` 截断。
    """
    from scripts._utils import expected_trading_day

    date_str = date_str or expected_trading_day()
    cache_dir = get_cache_db_path().parent
    buffett_db = cache_dir / "buffett_cache.db"
    meta = load_metadata_index(cache_dir)
    universe = _load_universe(meta)

    fundamentals = load_fundamentals(buffett_db, date_str)
    spot = fetch_spot_snapshot(cache_dir, date_str) if fetch else {}

    # ── 第 1 关：逻辑 ──
    passed: list[tuple[Any, float]] = []
    reject_logic: dict[str, int] = {}
    for code, _ticker in universe.items():
        m = fundamentals.get(ticker_to_thscode(code))
        if m is None:
            reject_logic["无基本面"] = reject_logic.get("无基本面", 0) + 1
            continue
        ok, logic_score, reason = score_logic(m)
        if not ok:
            key = reason.split(" ")[0]
            reject_logic[key] = reject_logic.get(key, 0) + 1
            continue
        passed.append((m, logic_score))
    logger.info(f"第 1 关（逻辑）：通过 {len(passed)} 只")
    for reason, n in sorted(reject_logic.items(), key=lambda x: -x[1])[:6]:
        logger.info(f"    · {reason}: {n} 只")

    if not passed:
        logger.warning("无股票通过逻辑门槛")
        return [], {
            "universe": len(universe),
            "logic_pass": 0,
            "abs_val_pass": 0,
            "val_pass": 0,
            "trend_pass": 0,
        }

    # ── 第 2 关：绝对估值 PE/PB ──
    abs_pass: list[tuple[Any, float, float, float]] = []
    reject_abs: dict[str, int] = {}
    pe_pb = _load_pe_pb(buffett_db)
    for m, logic_score in passed:
        code = m.code
        pe, pb = pe_pb.get(code, (None, None))
        if pe is None or not (0 < pe < MAX_PE) or pb is None or not (0 < pb < MAX_PB):
            reject_abs["PE/PB越界"] = reject_abs.get("PE/PB越界", 0) + 1
            continue
        if min_cap and spot:
            cap = spot.get(code, {}).get("market_cap_yi")
            if cap is None or cap < min_cap:
                reject_abs["市值不足"] = reject_abs.get("市值不足", 0) + 1
                continue
        abs_pass.append((m, logic_score, pe, pb))
    logger.info(
        f"第 2 关（绝对估值 PE∈(0,{MAX_PE:.0f})、PB∈(0,{MAX_PB:.0f}))：通过 {len(abs_pass)} 只"
    )
    logger.info(f"    · 淘汰 {sum(reject_abs.values())} 只（PE/PB 缺失或越界）")

    # 五年分位（联网）
    abs_pass.sort(key=lambda x: -x[1])
    abs_val_pass_total = len(abs_pass)
    abs_pass = abs_pass[:max_candidates]
    hist: dict[str, dict[str, float]] = {}
    if fetch and abs_pass:
        hist = fetch_valuation_history([x[0].code for x in abs_pass])

    val_pass: list[
        tuple[Any, float, float, float, dict[str, float] | None, float, str]
    ] = []
    reject_val: dict[str, int] = {}
    for m, logic_score, pe, pb in abs_pass:
        h = hist.get(m.code)
        ok, v_score, detail = score_valuation(pe, pb, h, m.profit_cagr)
        if not ok:
            key = detail.split(" ")[0]
            reject_val[key] = reject_val.get(key, 0) + 1
            continue
        val_pass.append((m, logic_score, pe, pb, h, v_score, detail))
    logger.info(f"第 2 关（估值分位/PEG）：通过 {len(val_pass)} 只")
    for reason, n in sorted(reject_val.items(), key=lambda x: -x[1])[:6]:
        logger.info(f"    · {reason}: {n} 只")

    if not val_pass:
        logger.warning("无股票通过逻辑+估值门槛")
        return [], {
            "universe": len(universe),
            "logic_pass": len(passed),
            "abs_val_pass": abs_val_pass_total,
            "val_pass": 0,
            "trend_pass": 0,
        }

    # ── 技术面（位置/趋势/量价） ──
    tickers = {x[0].code: normalize_ticker_key(x[0].code) for x in val_pass}
    tech_map = load_technicals(tickers, get_cache_db_path(), asof=date_str)
    latest_kline_date = (
        date.fromtimestamp(max(t["last_bar_ts"] for t in tech_map.values())).isoformat()
        if tech_map
        else date_str
    )

    # ── 股东户数 ──
    holders = fetch_holder_counts() if fetch else {}

    results: list[StockScore] = []
    for m, logic_score, pe, pb, h, v_score, v_detail in val_pass:
        tech = tech_map.get(m.code)
        if tech is None:
            continue
        ok, t_score, t_detail = trend_gate_and_score(tech)
        if not ok:
            continue
        pos_score, pos_detail = score_position(tech)
        vol_score, vol_detail = score_vol_price(tech)
        h_score, h_detail = score_holder(holders.get(m.code))

        item = StockScore(
            code=m.code,
            name=m.name,
            ticker=normalize_ticker_key(m.code),
            logic=logic_score,
            valuation=v_score,
            position=pos_score,
            trend=t_score,
            holder=h_score,
            vol_price=vol_score,
        )
        item.composite = sum(getattr(item, k) * w for k, w in DIM_WEIGHTS)
        item.metrics = {
            "pe": round(pe, 2),
            "pb": round(pb, 2),
            "pe_pct": None if not h or "pe_pct" not in h else round(h["pe_pct"], 1),
            "pb_pct": None if not h or "pb_pct" not in h else round(h["pb_pct"], 1),
            "peg": round(pe / m.profit_cagr, 2) if m.profit_cagr > 0 else None,
            "roe_avg": round(m.roe_avg, 1),
            "roe_excl_avg": round(m.roe_excl_avg, 1),
            "profit_cagr": round(m.profit_cagr, 1),
            "revenue_cagr": round(m.revenue_cagr, 1),
            "cfo_ratio": None if m.cfo_ratio is None else round(m.cfo_ratio, 2),
            "debt_ratio": None if m.debt_ratio is None else round(m.debt_ratio, 1),
            "gross_margin": None if m.gross_margin is None else round(m.gross_margin, 1),
            "market_cap_yi": round(spot.get(m.code, {}).get("market_cap_yi", 0.0), 1),
            "pct_pos_3y": round(tech["pct_pos_3y"], 1),
            "pct_pos_1y": round(tech["pct_pos_1y"], 1),
            "ma60_slope": round(tech["ma60_slope"], 2),
            "amount_ratio": round(tech["amt_ratio"], 2),
            "ret20": round(tech["ret20"], 2),
            "holder_chg_pct": None if m.code not in holders else holders[m.code]["chg_pct"],
            "close": round(tech["close"], 2),
            "ma5": round(tech["ma5"], 2),
            "last_date": latest_kline_date,
        }
        logic_detail = (
            f"ROE均值 {m.roe_avg:.1f}%（扣非 {m.roe_excl_avg:.1f}%），"
            f"3年净利CAGR {m.profit_cagr:.1f}%，现金流/净利 {m.cfo_ratio:.2f}"
        )
        item.notes = {
            "logic": logic_detail,
            "valuation": v_detail,
            "position": pos_detail,
            "trend": t_detail,
            "holder": h_detail,
            "vol_price": vol_detail,
        }
        results.append(item)

    logger.info(f"技术面通过（未处于下跌趋势）：{len(results)} 只")
    sort_scores(results)
    funnel = {
        "universe": len(universe),
        "logic_pass": len(passed),
        "abs_val_pass": abs_val_pass_total,
        "val_pass": len(val_pass),
        "trend_pass": len(results),
    }
    return results, funnel
