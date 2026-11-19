"""scripts/a_stock_screen/screener.py — A股筛选器

实施参考 scripts/hk_screen 的港股筛选规则表，为 A股 提供对应的
流动性、市值、价格、停牌、ST 等硬过滤规则。

数据源：
  - K 线：本地缓存 (pipeline_cache.db)，用于计算近20日日均成交额、
    近30日停牌天数（成交量为0视为停牌）
  - 元数据：metadata.json，用于市值、PE/PB、名称（ST判断）、换手率等
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any  # 值类型混杂（float/int/bool/str/None），使用 Any 避免冗余 Union

import pandas as pd
from loguru import logger

from scripts._utils import (
    get_cache_db_path,
    get_sqlite_conn,
    is_bse_code,
    is_etf_ticker,
    is_st_name,
    load_metadata_index,
    normalize_ticker_key,
    strip_ticker_prefix,
)
from scripts.a_stock_screen.constants import (
    ASTOCK_FUNDAMENTAL_RULES,
    ASTOCK_LIQUIDITY_RULES,
    ASTOCK_TRADE_RULES,
)

CACHE_DB = get_cache_db_path()


@dataclass
class AStockScreenResult:
    """单只 A股 筛选结果"""

    code: str
    name: str = ""

    # 基础信息
    close: float | None = None
    market_cap: float | None = None
    pe_ttm: float | None = None
    pb: float | None = None

    # 流动性指标
    turnover_20d_avg: float | None = None
    turnover_rate_12m_avg: float | None = None

    # 上市/交易信息
    listing_days: int | None = None
    is_st: bool = False
    is_etf: bool = False
    is_bse: bool = False
    suspension_days_30d: int = 0

    # 财务指标（软过滤/加分参考）
    debt_to_asset: float | None = None
    gross_margin: float | None = None
    avg_roe_3y: float | None = None

    # 筛选结果
    pass_count: int = 0
    total_rules: int = 0
    fail_reasons: list[str] = field(default_factory=list)
    passed: bool = False

    def to_dict(self) -> dict[str, Any]:  # 返回字段值类型混杂，Any 为合理
        return {
            "code": self.code,
            "name": self.name,
            "close": self.close,
            "market_cap": self.market_cap,
            "pe_ttm": self.pe_ttm,
            "pb": self.pb,
            "turnover_20d_avg": self.turnover_20d_avg,
            "turnover_rate_12m_avg": self.turnover_rate_12m_avg,
            "listing_days": self.listing_days,
            "is_st": self.is_st,
            "is_etf": self.is_etf,
            "is_bse": self.is_bse,
            "suspension_days_30d": self.suspension_days_30d,
            "debt_to_asset": self.debt_to_asset,
            "gross_margin": self.gross_margin,
            "avg_roe_3y": self.avg_roe_3y,
            "pass_count": self.pass_count,
            "total_rules": self.total_rules,
            "fail_reasons": "; ".join(self.fail_reasons),
            "passed": self.passed,
        }


# ─────────────────────────────────────────────────────────────────────────────
# 数据获取辅助
# ─────────────────────────────────────────────────────────────────────────────


def _load_kline_full(conn: sqlite3.Connection, ticker: str) -> pd.DataFrame | None:
    """从缓存读取该 ticker 的完整日线数据（已按日期升序排列，复用传入连接）。"""
    row = conn.execute(
        "SELECT data FROM kline_cache WHERE ticker = ?", (ticker,)
    ).fetchone()
    if not row:
        return None
    try:
        from io import BytesIO

        df = pd.read_pickle(BytesIO(row[0]))
    except Exception as e:
        logger.debug(f"{ticker} K线读取失败: {e}")
        return None
    if df is None or df.empty:
        return None
    ts_col = "timestamps" if "timestamps" in df.columns else "date"
    df[ts_col] = pd.to_datetime(df[ts_col])
    df = df.sort_values(ts_col).reset_index(drop=True)
    return df


def _window_kline(df: pd.DataFrame | None, end_date: str, days: int) -> pd.DataFrame | None:
    """从已加载的完整日线中截取最近 days 个交易日的窗口。"""
    if df is None or df.empty:
        return None
    start = (datetime.strptime(end_date, "%Y-%m-%d") - timedelta(days=days + 10)).strftime(
        "%Y-%m-%d"
    )
    ts_col = "timestamps" if "timestamps" in df.columns else "date"
    mask = (df[ts_col] >= pd.Timestamp(start)) & (df[ts_col] <= pd.Timestamp(end_date))
    return df.loc[mask].reset_index(drop=True)


def _fetch_kline_window(
    conn: sqlite3.Connection, ticker: str, end_date: str, days: int
) -> pd.DataFrame | None:
    """从缓存读取最近 days 个交易日的日线数据（复用传入的连接，不再每只股票开关连接）。"""
    return _window_kline(_load_kline_full(conn, ticker), end_date, days)


def _compute_technical_from_kline(df: pd.DataFrame | None) -> dict[str, Any]:  # 值类型混杂，Any 为合理
    """从 K 线窗口计算：近20日日均成交额、近30日停牌天数、最新价。"""
    out: dict[str, Any] = {
        "close": None,
        "turnover_20d_avg": None,
        "suspension_days_30d": 0,
    }
    if df is None or df.empty:
        return out

    close_col = "close"
    vol_col = "volume" if "volume" in df.columns else None
    amt_col = "amount" if "amount" in df.columns else None

    closes = df[close_col].astype(float)
    out["close"] = float(closes.iloc[-1]) if len(closes) > 0 else None

    # 成交额：优先用 amount 列，否则 close * volume 估算
    if amt_col:
        amounts = df[amt_col].astype(float)
    elif vol_col:
        amounts = closes * df[vol_col].astype(float)
    else:
        amounts = None

    # 近 20 个交易日日均成交额
    if amounts is not None and len(amounts) > 0:
        recent_20d = amounts.tail(20)
        out["turnover_20d_avg"] = float(recent_20d.mean())

    # 近 30 个交易日停牌天数（成交量为 0 或极小视为停牌）
    if vol_col is not None and len(df) > 0:
        recent_30d = df[vol_col].astype(float).tail(30)
        out["suspension_days_30d"] = int((recent_30d <= 0).sum())

    return out


# ─────────────────────────────────────────────────────────────────────────────
# 规则判断
# ─────────────────────────────────────────────────────────────────────────────


def count_passed_rules(
    result: AStockScreenResult,
) -> tuple[int, int, list[str]]:
    """统计通过的规则数量和失败原因。

    数据缺失（None）时不计入失败，只在明确不满足条件时才算失败。

    Returns
    -------
    (passed_count, total_evaluated_rules, fail_reasons)
    """
    checks: list[tuple[bool, str]] = []

    def _check(condition: bool | None, reason: str) -> None:
        if condition is None:
            return
        checks.append((condition, reason))

    def _check_value(
        value: float | int | None,
        threshold: float | int,
        op: str,
        reason: str,
    ) -> None:
        if value is None:
            return
        if op == ">=":
            condition = value >= threshold
        elif op == "<=":
            condition = value <= threshold
        elif op == ">":
            condition = value > threshold
        elif op == "<":
            condition = value < threshold
        else:
            raise ValueError(f"未知操作符: {op}")
        checks.append((condition, reason))

    # 交易规则
    _check(not result.is_st, "ST / *ST / 退市股票")
    _check(not result.is_bse, "北交所股票")
    _check(not result.is_etf, "ETF 基金（以纯技术面参与）")
    _check_value(
        result.listing_days,
        ASTOCK_TRADE_RULES["min_listing_days"],
        ">=",
        f"上市时长不足{ASTOCK_TRADE_RULES['min_listing_days']}天",
    )
    _check(
        result.suspension_days_30d <= ASTOCK_TRADE_RULES["max_suspension_days_30d"],
        f"近30日停牌天数超{ASTOCK_TRADE_RULES['max_suspension_days_30d']}天",
    )
    _check_value(
        result.close,
        ASTOCK_TRADE_RULES["min_price"],
        ">=",
        f"最新股价低于{ASTOCK_TRADE_RULES['min_price']}元",
    )

    # 流动性与市值
    _check_value(
        result.turnover_20d_avg,
        ASTOCK_LIQUIDITY_RULES["min_avg_turnover_20d"],
        ">=",
        f"近20日日均成交额低于{ASTOCK_LIQUIDITY_RULES['min_avg_turnover_20d']:.0f}元",
    )
    _check_value(
        result.turnover_rate_12m_avg,
        ASTOCK_LIQUIDITY_RULES["min_avg_turnover_rate_12m"],
        ">=",
        f"近12个月日均换手率低于{ASTOCK_LIQUIDITY_RULES['min_avg_turnover_rate_12m']}%",
    )
    _check_value(
        result.market_cap,
        ASTOCK_LIQUIDITY_RULES["min_market_cap"],
        ">=",
        f"总市值低于{ASTOCK_LIQUIDITY_RULES['min_market_cap'] / 1e8:.0f}亿元",
    )

    # 财务基本面（软规则，数据缺失时不计入失败）
    _check_value(
        result.debt_to_asset,
        ASTOCK_FUNDAMENTAL_RULES["max_debt_to_asset"],
        "<=",
        f"资产负债率超{ASTOCK_FUNDAMENTAL_RULES['max_debt_to_asset']:.0%}",
    )
    _check_value(
        result.gross_margin,
        ASTOCK_FUNDAMENTAL_RULES["min_gross_margin"],
        ">=",
        f"主业毛利率低于{ASTOCK_FUNDAMENTAL_RULES['min_gross_margin']:.0%}",
    )
    _check_value(
        result.avg_roe_3y,
        ASTOCK_FUNDAMENTAL_RULES["min_avg_roe_3y"],
        ">=",
        f"近3年平均ROE低于{ASTOCK_FUNDAMENTAL_RULES['min_avg_roe_3y']:.0%}",
    )

    passed = sum(1 for ok, _ in checks if ok)
    failed = sum(1 for ok, _ in checks if not ok)
    total_evaluated = passed + failed
    fail_reasons = [reason for ok, reason in checks if not ok]

    return passed, total_evaluated, fail_reasons


# ─────────────────────────────────────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────────────────────────────────────


def screen_a_stock_candidates(
    codes: list[str],
    date_str: str,
    metadata: dict[str, dict[str, object]] | None = None,
) -> tuple[list[str], dict[str, AStockScreenResult]]:
    """对 A股 候选代码做硬过滤筛选。

    参考 hk_screen.screener.screen_hk_stocks 的流程，适配 A股 市场。

    Parameters
    ----------
    codes : list[str]
        6 位裸代码列表（如 ["600519", "000858"]）。
    date_str : str
        分析日期 YYYY-MM-DD，用于定位 K 线窗口。
    metadata : dict | None
        元数据索引（load_metadata_index 返回）。为 None 时自动加载。

    Returns
    -------
    (passed_codes, details)
        passed_codes: 通过筛选的 6 位代码列表
        details: {code: AStockScreenResult} 每只股票的筛选详情
    """
    if metadata is None:
        metadata = load_metadata_index()

    results: dict[str, AStockScreenResult] = {}

    conn = get_sqlite_conn(CACHE_DB)
    try:
        for code in codes:
            ticker = normalize_ticker_key(code)
        bare = strip_ticker_prefix(ticker)

        meta = metadata.get(ticker) or metadata.get(bare) or {}
        name = str(meta.get("name") or "")
        market_cap = meta.get("market_cap")
        pe_ttm = meta.get("pe_ttm")
        pb = meta.get("pb")
        turnover_rate = meta.get("turnover_rate") or meta.get("turnover_rate_12m_avg")

        # 股票类型判断
        is_etf = is_etf_ticker(ticker)
        is_bse = is_bse_code(bare)
        is_st = bool(meta.get("is_st")) or is_st_name(name)

        # 从 K 线读取：完整历史（用于上市时长）及最近 30 日窗口（用于流动性/停牌）
        df_full = _load_kline_full(conn, ticker)
        listing_days = int(len(df_full)) if df_full is not None else None
        kline = _window_kline(df_full, date_str, days=30)
        tech = _compute_technical_from_kline(kline)

        result = AStockScreenResult(
            code=code,
            name=name,
            close=tech.get("close"),
            market_cap=float(market_cap) if isinstance(market_cap, (int, float)) else None,
            pe_ttm=float(pe_ttm) if isinstance(pe_ttm, (int, float)) else None,
            pb=float(pb) if isinstance(pb, (int, float)) else None,
            turnover_20d_avg=tech.get("turnover_20d_avg"),
            turnover_rate_12m_avg=(
                float(turnover_rate) if isinstance(turnover_rate, (int, float)) else None
            ),
            is_st=is_st,
            is_etf=is_etf,
            is_bse=is_bse,
            listing_days=listing_days,
            suspension_days_30d=tech.get("suspension_days_30d", 0),
        )
        results[code] = result
    finally:
        conn.close()

    # 统计通过规则
    passed_codes: list[str] = []
    for code, result in results.items():
        passed_count, total_rules, fail_reasons = count_passed_rules(result)
        result.pass_count = passed_count
        result.total_rules = total_rules
        result.fail_reasons = fail_reasons
        result.passed = passed_count == total_rules and total_rules > 0
        if result.passed:
            passed_codes.append(code)

    # 按 pass_count 降序、fail_reasons 长度升序排列
    passed_codes.sort(key=lambda c: (-results[c].pass_count, len(results[c].fail_reasons)))

    logger.info(
        f"A股筛选：候选 {len(codes)} 只，通过 {len(passed_codes)} 只"
    )

    return passed_codes, results
