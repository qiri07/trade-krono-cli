"""fill_missing 选股 — 数据库查询。

包含 _has_sufficient_history / get_failed_tickers / get_today_only_tickers / _has_today_data
等从 pipeline_cache.db 查询数据的函数。
"""

from __future__ import annotations

from scripts._utils import get_sqlite_conn
from scripts.fill_missing.constants import CACHE_DB, FAILED_MIN_RECORDS


def _has_sufficient_history(ticker: str) -> bool:
    """检查 ticker 是否已有足够的历史数据（记录数 ≥ FAILED_MIN_RECORDS）。

    用于增量跳过：若数据库中该 ticker 已有足够记录，说明历史补拉已完成，
    无需再次拉取，避免中断重启后重复消耗 API 额度。

    Args:
        ticker: 带交易所前缀的 ticker，如 "sh.600519"。

    Returns:
        True 表示已有足够历史数据，可跳过；False 表示需要补拉。
    """
    conn = get_sqlite_conn(CACHE_DB)
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM kline_cache WHERE ticker = ?",
        (ticker,),
    )
    count = cur.fetchone()[0]
    conn.close()
    return count >= FAILED_MIN_RECORDS


def get_failed_tickers() -> list[str]:
    """动态查询 DB 中记录数过少的股票（全量同步失败或未补齐的历史数据）。

    使用 GROUP BY 替代子查询，避免对每行执行 COUNT(*) 导致的全表扫描。

    Returns:
        记录数 < FAILED_MIN_RECORDS 的非北交所 ticker 列表。
    """
    conn = get_sqlite_conn(CACHE_DB)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT ticker FROM kline_cache
        WHERE ticker NOT LIKE 'bj.%'
        GROUP BY ticker
        HAVING COUNT(*) < ?
        ORDER BY ticker
    """,
        (FAILED_MIN_RECORDS,),
    )
    tickers = [r[0] for r in cur.fetchall()]
    conn.close()
    if tickers:
        return tickers
    return []


def get_today_only_tickers() -> list[str]:
    """获取只有今日数据的非北交所股票（需要补全历史）。"""

    conn = get_sqlite_conn(CACHE_DB)
    cur = conn.cursor()
    from scripts.fill_missing.dates import get_today_date

    today = get_today_date()
    cur.execute(
        """
        SELECT DISTINCT ticker FROM kline_cache
        WHERE start = ? AND end = ?
          AND ticker NOT LIKE 'bj.%'
        ORDER BY ticker
    """,
        (today, today),
    )
    tickers = [r[0] for r in cur.fetchall()]
    conn.close()
    return tickers


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
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM kline_cache WHERE ticker = ? AND end >= ?",
        (ticker, today),
    )
    count = cur.fetchone()[0]
    conn.close()
    return count > 0
