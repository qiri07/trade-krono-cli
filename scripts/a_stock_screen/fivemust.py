"""scripts/a_stock_screen/fivemust.py — A股「五必」筛选器。

条件（量化标准）：
  1. 必须在历史低位：3 年收盘价分位数 ≤ 30%（历史分位，避免高位接盘）。
  2. 必须底部扎实：近 20 日波动率 ≤ 25%，且最近 5 日累计涨幅 ≤ 15%。
  3. 必须业绩优秀：ROE ≥ 12%、扣非 ROE ≥ 10%、净利润 TTM > 0、经营现金流 > 0。
  4. 必须形成筹码集中：股东户数环比减少（最新披露季度 vs 上一季度）。
  5. 必须形成日K多头排列且向上突破：
       MA5 > MA10 > MA20 > MA60，收盘价 > MA5，且收盘价 ≥ 近 20 日最高价。

用法：
    from scripts.a_stock_screen.fivemust import screen_five_must
    passed, details = screen_five_must(codes=["600519", "000858"], date_str="2026-11-20")
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any  # 行情/K 线/财务字段值类型混杂，Any 为合理

import pandas as pd
from loguru import logger

from scripts._utils import (
    get_cache_db_path,
    get_sqlite_conn,
    normalize_ticker_key,
    strip_ticker_prefix,
)

BUFFETT_CACHE_DB = Path("outputs/cache/buffett_cache.db")

# ════════════════════════════════════════════════════════════════════════════════
# 五必量化阈值（可按需调整）
# ════════════════════════════════════════════════════════════════════════════════

FIVEMUST_RULES = {
    # 条件 1：3 年价格分位数上限（≤30% 视为历史低位）
    "price_percentile_max": 0.30,
    # 看回窗口（交易日）：3 年 ≈ 750 个交易日
    "lookback_trading_days": 750,
    # 条件 2：底部扎实——近 20 日波动率上限（最高/最低-1）
    "max_20d_volatility": 0.25,
    # 最近 5 日累计涨幅上限
    "max_5d_return": 0.15,
    # 条件 3：业绩优秀——ROE / 扣非 ROE 下限（%）
    "min_roe": 12.0,
    "min_roe_excl": 10.0,
    # 条件 5：均线周期
    "ma_fast": 5,
    "ma_mid": 10,
    "ma_slow": 20,
    "ma_trend": 60,
    # 突破判定窗口（交易日）
    "breakout_window": 20,
}


@dataclass
class FiveMustResult:
    """单只股票五必筛选结果"""

    code: str
    name: str = ""

    # 基础行情
    close: float | None = None
    market_cap: float | None = None

    # 条件 1：历史低位
    price_percentile: float | None = None  # 3 年分位数 0~1
    cond1_history_low: bool = False
    cond1_reason: str = ""

    # 条件 2：底部扎实
    volatility_20d: float | None = None
    return_5d: float | None = None
    cond2_bottom_solid: bool = False
    cond2_reason: str = ""

    # 条件 3：业绩优秀
    roe: float | None = None
    roe_excl: float | None = None
    net_profit_ttm: float | None = None
    operating_cash_flow: float | None = None
    cond3_performance: bool = False
    cond3_reason: str = ""

    # 条件 4：筹码集中
    holder_count: float | None = None
    holder_count_prev: float | None = None
    holder_change_pct: float | None = None
    cond4_concentration: bool = False
    cond4_reason: str = ""

    # 条件 5：日K多头+突破
    ma5: float | None = None
    ma10: float | None = None
    ma20: float | None = None
    ma60: float | None = None
    high_20d: float | None = None
    cond5_breakout: bool = False
    cond5_reason: str = ""

    # 汇总
    pass_count: int = 0
    total_rules: int = 5
    fail_reasons: list[str] = field(default_factory=list)
    passed: bool = False

    @property
    def conditions_passed(self) -> int:
        return sum(
            1
            for c in (
                self.cond1_history_low,
                self.cond2_bottom_solid,
                self.cond3_performance,
                self.cond4_concentration,
                self.cond5_breakout,
            )
            if c
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "close": self.close,
            "market_cap": self.market_cap,
            "price_percentile": self.price_percentile,
            "cond1_history_low": self.cond1_history_low,
            "cond1_reason": self.cond1_reason,
            "volatility_20d": self.volatility_20d,
            "return_5d": self.return_5d,
            "cond2_bottom_solid": self.cond2_bottom_solid,
            "cond2_reason": self.cond2_reason,
            "roe": self.roe,
            "roe_excl": self.roe_excl,
            "net_profit_ttm": self.net_profit_ttm,
            "operating_cash_flow": self.operating_cash_flow,
            "cond3_performance": self.cond3_performance,
            "cond3_reason": self.cond3_reason,
            "holder_count": self.holder_count,
            "holder_change_pct": self.holder_change_pct,
            "cond4_concentration": self.cond4_concentration,
            "cond4_reason": self.cond4_reason,
            "ma5": self.ma5,
            "ma10": self.ma10,
            "ma20": self.ma20,
            "ma60": self.ma60,
            "high_20d": self.high_20d,
            "cond5_breakout": self.cond5_breakout,
            "cond5_reason": self.cond5_reason,
            "pass_count": self.pass_count,
            "total_rules": self.total_rules,
            "fail_reasons": "; ".join(self.fail_reasons),
            "passed": self.passed,
        }


# ─────────────────────────────────────────────────────────────────────────────
# 条件 1 & 2：历史低位 / 底部扎实（基于本地 K 线）
# ─────────────────────────────────────────────────────────────────────────────


def _load_kline_to_date(
    conn: sqlite3.Connection, ticker: str, date_str: str
) -> pd.DataFrame | None:
    """从 kline_cache 读取单只股票至 date_str 的日线（升序，不含 date_str 之后）。"""
    row = conn.execute(
        "SELECT data FROM kline_cache WHERE ticker = ? ORDER BY end DESC LIMIT 1",
        (ticker,),
    ).fetchone()
    if not row or row[0] is None:
        return None
    try:
        df = pd.read_pickle(BytesIO(row[0]))
    except (OSError, ValueError, EOFError) as exc:
        logger.debug(f"{ticker} K 线反序列化失败: {exc}")
        return None
    if df is None or df.empty:
        return None
    ts_col = "timestamps" if "timestamps" in df.columns else "timestamp"
    df = df.copy()
    df[ts_col] = pd.to_datetime(df[ts_col])
    df = df.sort_values(ts_col).reset_index(drop=True)
    df = df.loc[df[ts_col] <= pd.Timestamp(date_str)]
    return df


def _eval_cond1_history_low(
    df: pd.DataFrame | None, date_str: str, rules: dict[str, Any]
) -> tuple[bool, str, float | None]:
    """条件 1：历史低位。

    3 年收盘价分位数 = (当前收盘价 - 3 年最低价) / (3 年最高价 - 3 年最低价)。
    分位数 ≤ rules["price_percentile_max"] 时通过。
    """
    max_pct = float(rules["price_percentile_max"])
    lookback = int(rules["lookback_trading_days"])
    if df is None or df.empty or "close" not in df.columns:
        return False, "无 K 线数据", None
    closes = df["close"].astype(float)
    if len(closes) < 20:
        return False, "K 线数据不足（<20 根）", None
    window = closes.tail(lookback) if len(closes) > lookback else closes
    cur = float(closes.iloc[-1])
    lo = float(window.min())
    hi = float(window.max())
    if hi <= lo:
        return False, "3 年价格无波动", None
    pct = (cur - lo) / (hi - lo)
    ok = pct <= max_pct
    return (
        ok,
        "" if ok else f"3年价格分位 {pct:.0%} > {max_pct:.0%}",
        pct,
    )


def _eval_cond2_bottom_solid(df: pd.DataFrame | None, rules: dict[str, Any]) -> tuple[bool, str, float | None, float | None]:
    """条件 2：底部扎实。

    - 近 20 日波动率 = (20 日最高 - 20 日最低) / 20 日最低 ≤ max_20d_volatility
    - 最近 5 日累计涨幅 ≤ max_5d_return
    """
    max_vol = float(rules["max_20d_volatility"])
    max_ret = float(rules["max_5d_return"])
    if df is None or df.empty or "close" not in df.columns or "high" not in df.columns or "low" not in df.columns:
        return False, "无 K 线数据", None, None
    closes = df["close"].astype(float)
    highs = df["high"].astype(float)
    lows = df["low"].astype(float)
    if len(closes) < 21:
        return False, "K 线数据不足（<21 根）", None, None
    high_20 = float(highs.tail(20).max())
    low_20 = float(lows.tail(20).min())
    vol = (high_20 - low_20) / low_20 if low_20 > 0 else 1.0
    cur = float(closes.iloc[-1])
    prev_5 = float(closes.iloc[-6])
    ret5 = (cur - prev_5) / prev_5 if prev_5 > 0 else 1.0
    reasons: list[str] = []
    if vol > max_vol:
        reasons.append(f"20日波动率 {vol:.0%} > {max_vol:.0%}")
    if ret5 > max_ret:
        reasons.append(f"5日涨幅 {ret5:.0%} > {max_ret:.0%}")
    ok = not reasons
    return (
        ok,
        "; ".join(reasons) if reasons else "",
        vol,
        ret5,
    )


def _eval_cond3_performance(
    fin_latest: dict[str, Any] | None,
    cfo: float | None,
) -> tuple[bool, str, float | None, float | None, float | None, float | None]:
    """条件 3：业绩优秀。

    ROE ≥ min_roe、扣非 ROE ≥ min_roe_excl、净利润 TTM > 0、经营现金流 > 0。
    """
    min_roe = float(FIVEMUST_RULES["min_roe"])
    min_roe_excl = float(FIVEMUST_RULES["min_roe_excl"])
    roe: float | None = None
    roe_excl: float | None = None
    net_profit_ttm: float | None = None
    reasons: list[str] = []

    if fin_latest is None:
        return False, "无财务数据", None, None, None, None
    roe = fin_latest.get("roe")
    roe_excl = fin_latest.get("roe_excl")
    if roe is not None and roe < min_roe:
        reasons.append(f"ROE {roe:.1f}% < {min_roe:.1f}%")
    if roe_excl is not None and roe_excl < min_roe_excl:
        reasons.append(f"扣非ROE {roe_excl:.1f}% < {min_roe_excl:.1f}%")
    if net_profit_ttm is not None and net_profit_ttm <= 0:
        reasons.append(f"净利润TTM {net_profit_ttm:,.0f} <= 0")
    if cfo is not None and cfo <= 0:
        reasons.append(f"经营现金流 {cfo:,.0f} <= 0")

    ok = not reasons
    return ok, "; ".join(reasons) if reasons else "", roe, roe_excl, net_profit_ttm, cfo


def _eval_cond4_concentration(
    holder_curr: float | None, holder_prev: float | None
) -> tuple[bool, str, float | None]:
    """条件 4：筹码集中——股东户数环比减少。

    股东户数本次 < 上次，说明股东在减少，筹码集中，疑似主力高控盘。
    """
    change_pct: float | None = None
    if holder_curr is None or holder_prev is None or holder_prev <= 0:
        return False, "无股东户数数据", None
    change_pct = (holder_curr - holder_prev) / holder_prev
    ok = holder_curr < holder_prev
    return (
        ok,
        "" if ok else f"股东户数环比 {change_pct:.0%} 未减少",
        change_pct,
    )


def _eval_cond5_breakout(
    df: pd.DataFrame | None, rules: dict[str, Any]
) -> tuple[bool, str, float | None, float | None, float | None, float | None, float | None]:
    """条件 5：日K多头排列且向上突破。

    MA5 > MA10 > MA20 > MA60，收盘价 > MA5，且收盘价 ≥ 近 20 日最高价。
    """
    m_fast = int(rules["ma_fast"])
    m_mid = int(rules["ma_mid"])
    m_slow = int(rules["ma_slow"])
    m_trend = int(rules["ma_trend"])
    window = int(rules["breakout_window"])
    if (
        df is None
        or df.empty
        or "close" not in df.columns
        or len(df) < m_trend + window
    ):
        return False, "K 线数据不足", None, None, None, None, None
    close = df["close"].astype(float)
    cur = float(close.iloc[-1])
    ma5 = float(close.rolling(m_fast).mean().iloc[-1])
    ma10 = float(close.rolling(m_mid).mean().iloc[-1])
    ma20 = float(close.rolling(m_slow).mean().iloc[-1])
    ma60 = float(close.rolling(m_trend).mean().iloc[-1])
    high_20 = float(df["high"].astype(float).tail(window).max()) if "high" in df.columns else None
    reasons: list[str] = []
    if not (ma5 > ma10 > ma20 > ma60):
        reasons.append(f"非多头排列 MA5={ma5:.2f} MA10={ma10:.2f} MA20={ma20:.2f} MA60={ma60:.2f}")
    if cur <= ma5:
        reasons.append(f"收盘 {cur:.2f} ≤ MA5 {ma5:.2f}")
    if high_20 is not None and cur < high_20:
        reasons.append(f"未突破20日高 {cur:.2f} < {high_20:.2f}")
    ok = not reasons
    return ok, "; ".join(reasons) if reasons else "", ma5, ma10, ma20, ma60, high_20


def _thscode(bare: str) -> str:
    """把 6 位裸代码转成 buffett_cache 的 thscode（如 600519.SH / 000858.SZ）。"""
    if bare.startswith("6"):
        return f"{bare}.SH"
    return f"{bare}.SZ"


def _load_financial_cfo(
    conn_b: sqlite3.Connection, bare: str
) -> tuple[dict[str, Any] | None, float | None]:
    """从 buffett_cache 读取最新财务指标和经营现金流。

    - cache_financials: key 如 fin_600519.SH_2025-4，取 year-quarter 最大的一条
    - cache_cfo: key 如 cfo_600519.SH
    """
    thscode = _thscode(bare)
    fin_latest: dict[str, Any] | None = None
    cfo: float | None = None

    # 最新财务：按 key 中的 year-quarter 排序取最大
    rows = conn_b.execute(
        "SELECT key, data FROM cache_financials WHERE key LIKE ?",
        (f"fin_{thscode}_%",),
    ).fetchall()
    best_key = ""
    for key, data in rows:
        try:
            suffix = key.rsplit("_", 1)[-1]  # 2025-4
            y, q = suffix.split("-")
            yq = (int(y), int(q))
        except (ValueError, IndexError):
            continue
        if yq > (0, 0) and (not best_key or yq > _parse_yq(best_key)):
            best_key = key
            try:
                fin_latest = json.loads(data) if isinstance(data, str) else data
            except (json.JSONDecodeError, TypeError):
                fin_latest = None

    # 经营现金流
    row = conn_b.execute(
        "SELECT data FROM cache_cfo WHERE key = ?", (f"cfo_{thscode}",)
    ).fetchone()
    if row and row[0]:
        try:
            cfo_data = json.loads(row[0]) if isinstance(row[0], str) else row[0]
            cfo = cfo_data.get("cfo") if isinstance(cfo_data, dict) else None
        except (json.JSONDecodeError, TypeError):
            cfo = None

    return fin_latest, cfo


def _parse_yq(key: str) -> tuple[int, int]:
    """解析 key 中的 year-quarter，用于比较。"""
    try:
        suffix = key.rsplit("_", 1)[-1]
        y, q = suffix.split("-")
        return int(y), int(q)
    except (ValueError, IndexError):
        return (0, 0)


def _safe_float(val: Any) -> float | None:
    """安全转为 float，失败返回 None。"""
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def screen_five_must(
    codes: list[str],
    date_str: str,
) -> tuple[list[str], dict[str, FiveMustResult]]:
    """对 A股 候选做「五必」筛选。

    Parameters
    ----------
    codes : list[str]
        6 位裸代码列表（如 ["600519", "000858"]）。
    date_str : str
        分析日期 YYYY-MM-DD。

    Returns
    -------
    (passed_codes, details)
        passed_codes: 通过全部 5 条的 6 位代码列表
        details: {code: FiveMustResult} 每只股票的筛选详情
    """
    # 第 2–4 批：条件 1–2 使用本地 K 线；条件 3–5 先占位，第 4 批串联
    results: dict[str, FiveMustResult] = {}
    conn = get_sqlite_conn(get_cache_db_path())
    conn_b = get_sqlite_conn(str(BUFFETT_CACHE_DB))
    # 股东户数映射：{bare_code: (curr, prev)}，条件 4 使用
    holder_map: dict[str, tuple[float | None, float | None]] = {}
    try:
        import akshare as ak  # noqa: PLC0415

        df_holder = ak.stock_zh_a_gdhs()
        if df_holder is not None and not df_holder.empty:
            for _, r in df_holder.iterrows():
                code = str(r.get("代码", "")).strip()
                curr = r.get("股东户数-本次")
                prev = r.get("股东户数-上次")
                if code:
                    holder_map[code] = (
                        _safe_float(curr),
                        _safe_float(prev),
                    )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"⚠️ 股东户数获取失败（条件4将标记为未通过）: {exc}")
    try:
        for code in codes:
            ticker = normalize_ticker_key(code)
            bare = strip_ticker_prefix(ticker)
            result = FiveMustResult(code=code)

            df = _load_kline_to_date(conn, ticker, date_str)

            # 条件 1：历史低位
            ok1, reason1, pct = _eval_cond1_history_low(df, date_str, FIVEMUST_RULES)
            result.cond1_history_low = ok1
            result.cond1_reason = reason1
            result.price_percentile = pct

            # 条件 2：底部扎实
            ok2, reason2, vol, ret5 = _eval_cond2_bottom_solid(df, FIVEMUST_RULES)
            result.cond2_bottom_solid = ok2
            result.cond2_reason = reason2
            result.volatility_20d = vol
            result.return_5d = ret5

            if df is not None and not df.empty and "close" in df.columns:
                result.close = float(df["close"].iloc[-1])

            # 条件 3：业绩优秀（buffett_cache）
            fin_latest, cfo = _load_financial_cfo(conn_b, bare)
            ok3, reason3, roe, roe_excl, net_profit_ttm, cfo_val = _eval_cond3_performance(
                fin_latest, cfo
            )
            result.cond3_performance = ok3
            result.cond3_reason = reason3
            result.roe = roe
            result.roe_excl = roe_excl
            result.net_profit_ttm = net_profit_ttm
            result.operating_cash_flow = cfo_val

            # 条件 4：筹码集中（股东户数环比减少）
            holder_curr = holder_map.get(bare, (None, None))[0]
            holder_prev = holder_map.get(bare, (None, None))[1]
            ok4, reason4, change_pct = _eval_cond4_concentration(holder_curr, holder_prev)
            result.cond4_concentration = ok4
            result.cond4_reason = reason4
            result.holder_count = holder_curr
            result.holder_count_prev = holder_prev
            result.holder_change_pct = change_pct

            # 条件 5：日K多头排列且向上突破
            ok5, reason5, ma5, ma10, ma20, ma60, high_20 = _eval_cond5_breakout(
                df, FIVEMUST_RULES
            )
            result.cond5_breakout = ok5
            result.cond5_reason = reason5
            result.ma5 = ma5
            result.ma10 = ma10
            result.ma20 = ma20
            result.ma60 = ma60
            result.high_20d = high_20

            results[bare] = result
    finally:
        conn_b.close()
        conn.close()

    # 统计通过规则（条件 4–5 暂不计入，第 4 批串联）
    passed_codes: list[str] = []
    for code, result in results.items():
        fail_reasons: list[str] = []
        if not result.cond1_history_low and result.cond1_reason:
            fail_reasons.append(result.cond1_reason)
        if not result.cond2_bottom_solid and result.cond2_reason:
            fail_reasons.append(result.cond2_reason)
        if not result.cond3_performance and result.cond3_reason:
            fail_reasons.append(result.cond3_reason)
        if not result.cond4_concentration and result.cond4_reason:
            fail_reasons.append(result.cond4_reason)
        if not result.cond5_breakout and result.cond5_reason:
            fail_reasons.append(result.cond5_reason)
        result.fail_reasons = fail_reasons
        result.passed = (
            result.cond1_history_low
            and result.cond2_bottom_solid
            and result.cond3_performance
            and result.cond4_concentration
            and result.cond5_breakout
            and len(fail_reasons) == 0
        )
        if result.passed:
            passed_codes.append(code)

    passed_codes.sort(key=lambda c: len(results[c].fail_reasons))
    logger.info(
        f"五必筛选（全部5条）：候选 {len(codes)} 只，通过 {len(passed_codes)} 只"
    )
    return passed_codes, results
