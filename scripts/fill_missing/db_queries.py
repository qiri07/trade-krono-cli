"""fill_missing 选股 — 数据库查询。

包含 _has_sufficient_history / get_failed_tickers / get_today_only_tickers / _has_today_data
等从 pipeline_cache.db 查询数据的函数。

注意 ``kline_cache`` 的存储形态：**每个 ticker 通常只有 1 行**（主键为
``(ticker, start, end, freq, adjustflag)``），整段日线以 pickle 序列化在 ``data`` 列内。
因此「历史记录数」必须统计**行内 K 线根数**（反序列化后 ``len(df)``），
不能统计表行数 ``COUNT(*)`` —— 后者对每只票恒为 1，会把全市场都误判为记录不足。
"""

from __future__ import annotations

import pickle
import sqlite3
from datetime import date
from io import BytesIO
from pathlib import Path

import pandas as pd
from loguru import logger

from scripts._utils import FAILED_MIN_RECORDS, get_sqlite_conn
from scripts.fill_missing.constants import CACHE_DB, FULL_HISTORY_START, HISTORY_DENSITY_MIN
from scripts.fill_missing.dates import get_expected_date

# 反序列化可能抛出的异常：pickle 损坏、截断、类缺失、以及 IO/格式问题
_PICKLE_ERRORS = (
    pickle.UnpicklingError,
    EOFError,
    AttributeError,
    ImportError,
    ValueError,
    OSError,
)


def _count_bars(conn: sqlite3.Connection, ticker: str) -> int:
    """统计单个 ticker 在 ``kline_cache`` 中的真实日线根数。

    反序列化 ``data`` 列（pandas pickle）后取行数并累加（同一 ticker 存在多段记录时
    逐段相加）；数据损坏或无法解析的段按 0 根计并记录 warning
    （该 ticker 会被判定为历史不足，从而触发重新补拉）。

    Args:
        conn: 已打开的 ``kline_cache`` 数据库连接。
        ticker: 带交易所前缀的 ticker，如 "sh.600519"。

    Returns:
        日线根数；无记录或无法解析时返回 0。
    """
    cur = conn.cursor()
    cur.execute("SELECT data FROM kline_cache WHERE ticker = ?", (ticker,))
    total = 0
    for (blob,) in cur.fetchall():
        if blob is None:
            continue
        try:
            df: pd.DataFrame = pd.read_pickle(BytesIO(blob))
        except _PICKLE_ERRORS as exc:
            logger.warning("K 线数据无法反序列化，该段按 0 根计: ticker={} err={}", ticker, exc)
            continue
        total += len(df)
    return total


def _expected_trading_days(start: str, end: str) -> float:
    """按日历跨度估算区间内的交易日数（A 股一年约 250 个交易日 / 365 天）。"""
    span = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
    return max(span * 250 / 365, 1.0)


def _is_history_sufficient(
    conn: sqlite3.Connection, ticker: str, earliest: str, latest: str
) -> bool:
    """判定单个 ticker 的历史数据是否已足够（预筛 + 根数 + 密度）。

    判定顺序（先廉价后精确）：
    1. ``earliest`` 不晚于 ``FULL_HISTORY_START`` → 已覆盖完整回补窗口，直接判为充足；
    2. 真实根数 ≥ ``FAILED_MIN_RECORDS`` → 充足；
    3. 否则按**密度**兜底：次新股 / 次新 ETF 上市仅数月，根数天然少于阈值，
       但只要首末交易日内数据连续（密度 ≥ ``HISTORY_DENSITY_MIN``）即视为完整；
       抓取失败留下的稀疏片段（中间有大量空洞）密度低，判为不足。

    Args:
        conn: 已打开的 ``kline_cache`` 数据库连接。
        ticker: 带交易所前缀的 ticker。
        earliest: 该 ticker 在库中的最早 K 线日期（``MIN(start)``）。
        latest: 该 ticker 在库中的最晚 K 线日期（``MAX(end)``）。

    Returns:
        True 表示历史数据已足够。
    """
    if earliest <= FULL_HISTORY_START:
        return True
    bars = _count_bars(conn, ticker)
    if bars >= FAILED_MIN_RECORDS:
        return True
    try:
        dense_enough = bars >= HISTORY_DENSITY_MIN * _expected_trading_days(earliest, latest)
    except ValueError:
        logger.warning(
            "K 线日期格式异常，按历史不足处理: ticker={} {}~{}", ticker, earliest, latest
        )
        return False
    return dense_enough


def _has_sufficient_history(ticker: str) -> bool:
    """检查 ticker 是否已有足够的历史数据（按行内真实 K 线根数 + 密度判定）。

    用于增量跳过：若数据库中该 ticker 已有足够日线，说明历史补拉已完成，
    无需再次拉取，避免中断重启后重复消耗 API 额度。

    Args:
        ticker: 带交易所前缀的 ticker，如 "sh.600519"。

    Returns:
        True 表示已有足够历史数据，可跳过；False 表示需要补拉。
    """
    conn = get_sqlite_conn(CACHE_DB)
    try:
        cur = conn.cursor()
        cur.execute("SELECT MIN(start), MAX(end) FROM kline_cache WHERE ticker = ?", (ticker,))
        row = cur.fetchone()
        if not row or row[0] is None:
            return False
        return _is_history_sufficient(conn, ticker, row[0], row[1] or row[0])
    finally:
        conn.close()


def get_insufficient_history_tickers(db_path: Path | str | None = None) -> list[str]:
    """查询历史 K 线明显不足的股票（稀疏片段 / 未补齐历史）。

    历史上此处用 ``HAVING COUNT(*) < FAILED_MIN_RECORDS`` 统计**表行数**，
    而每只票通常只有 1 行，导致全市场（近万只）都被判为「历史不足」，
    补拉计划因此包含大量无需补拉的 ticker。

    为控制耗时，先按 ``MIN(start)`` 廉价预筛：起始日不晚于 ``FULL_HISTORY_START``
    的 ticker 必然已覆盖完整回补窗口，直接放行；其余（新上市 / 港股短窗口 /
    抓取失败）再反序列化 pickle 精确计数，并结合密度判定
    （详见 :func:`_is_history_sufficient`）。

    Args:
        db_path: 缓存数据库路径；``None`` 时在调用时解析默认的
            pipeline_cache.db（不用函数默认值，避免导入期固化路径而无法测试替换）。

    Returns:
        历史数据明显不足的 ticker 列表（升序），不含北交所。
    """
    conn = get_sqlite_conn(CACHE_DB if db_path is None else db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT ticker, MIN(start), MAX(end) FROM kline_cache
            WHERE ticker NOT LIKE 'bj.%'
            GROUP BY ticker
            ORDER BY ticker
            """
        )
        candidates = [
            (ticker, earliest, latest)
            for ticker, earliest, latest in cur.fetchall()
            if earliest is not None and earliest > FULL_HISTORY_START
        ]
        insufficient = [
            ticker
            for ticker, earliest, latest in candidates
            if not _is_history_sufficient(conn, ticker, earliest, latest or earliest)
        ]
    finally:
        conn.close()
    return insufficient


def get_failed_tickers() -> list[str]:
    """动态查询历史数据不足的股票（全量同步失败或未补齐的历史数据）。

    历史名称保留兼容；语义等同 :func:`get_insufficient_history_tickers`。

    Returns:
        历史数据明显不足的非北交所 ticker 列表。
    """
    return get_insufficient_history_tickers()


def get_today_only_tickers() -> list[str]:
    """获取只有预期交易日一天数据的非北交所股票（需要补全历史）。

    以「应已落库的最近交易日」（:func:`get_expected_date`）为准，而非原始今日：
    开盘前运行时当日 K 线尚不存在，用原始今日会永远查不到记录。

    Returns:
        首尾日期同为预期交易日的非北交所 ticker 列表。
    """
    conn = get_sqlite_conn(CACHE_DB)
    try:
        cur = conn.cursor()
        expected = get_expected_date()
        cur.execute(
            """
            SELECT DISTINCT ticker FROM kline_cache
            WHERE start = ? AND end = ?
              AND ticker NOT LIKE 'bj.%'
            ORDER BY ticker
            """,
            (expected, expected),
        )
        return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()


def _has_today_data(ticker: str, today: str) -> bool:
    """检查 ticker 是否已有今日数据（end ≥ today）。

    用于增量跳过：若数据库中该 ticker 已有今日 K 线，说明今日增量已同步，
    无需再次拉取。

    Args:
        ticker: 带交易所前缀的 ticker，如 "sh.600519"。
        today: 今日日期（YYYY-MM-DD）。

    Returns:
        True 表示已有今日数据，可跳过；False 表示需要补今日。
    """
    conn = get_sqlite_conn(CACHE_DB)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM kline_cache WHERE ticker = ? AND end >= ?",
            (ticker, today),
        )
        count = cur.fetchone()[0]
    finally:
        conn.close()
    return count > 0
