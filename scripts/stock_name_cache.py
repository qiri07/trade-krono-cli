"""scripts.stock_name_cache — 股票名称↔代码映射的本地 SQLite 缓存。

将股票名称与代码的对应关系持久化到 ``pipeline_cache.db`` 的
``stock_name_cache`` 表，实现：

- **命中即本地使用**：查名称时优先从 DB 读，命中不联网，零延迟
- **每日仅补漏**：每日流程更新时只对 DB 中缺失的 ticker 做逐只查询写入，
  不全量重拉，避免重复联网和会话卡死
- **幂等安全**：表创建与写入均为幂等，重复执行不重复数据

表结构：
  stock_name_cache(ticker TEXT PRIMARY KEY, name TEXT NOT NULL,
                   updated_at TEXT NOT NULL)
  ticker 为带交易所前缀，如 ``sh.600519`` / ``sz.000858``。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import datetime

from loguru import logger

from scripts._utils import CACHE_DB, get_sqlite_conn

STOCK_NAME_TABLE = "stock_name_cache"


def ensure_stock_name_table(conn: sqlite3.Connection) -> None:
    """确保 ``stock_name_cache`` 表存在（幂等，可重复调用）。

    Args:
        conn: SQLite 连接（已打开，由调用方负责 commit/close）。
    """
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {STOCK_NAME_TABLE} (
            ticker    TEXT PRIMARY KEY,  -- 带交易所前缀，如 sh.600519
            name      TEXT NOT NULL,     -- 股票名称
            updated_at TEXT NOT NULL     -- 写入时间 YYYY-MM-DD HH:MM:SS
        )
        """
    )
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{STOCK_NAME_TABLE}_updated_at "
        f"ON {STOCK_NAME_TABLE}(updated_at)"
    )
    conn.commit()


def get_cached_stock_name(conn: sqlite3.Connection, ticker: str) -> str | None:
    """从本地缓存读取股票名称。

    Args:
        conn: SQLite 连接。
        ticker: 带交易所前缀的 ticker，如 ``sh.600519``。

    Returns:
        股票名称；未命中返回 None。
    """
    cur = conn.execute(
        f"SELECT name FROM {STOCK_NAME_TABLE} WHERE ticker = ?", (ticker,)
    )
    row = cur.fetchone()
    return row[0] if row else None


def batch_get_cached_stock_names(
    conn: sqlite3.Connection, tickers: list[str]
) -> dict[str, str]:
    """批量从本地缓存读取名称，仅返回命中项。

    Args:
        conn: SQLite 连接。
        tickers: 带交易所前缀的 ticker 列表。

    Returns:
        {ticker: name} 命中项的映射。
    """
    if not tickers:
        return {}
    placeholders = ",".join("?" for _ in tickers)
    cur = conn.execute(
        f"SELECT ticker, name FROM {STOCK_NAME_TABLE} WHERE ticker IN ({placeholders})",
        tickers,
    )
    return {r[0]: r[1] for r in cur.fetchall()}


def batch_write_stock_name_cache(
    conn: sqlite3.Connection, rows: list[tuple[str, str]]
) -> int:
    """批量写入/更新名称缓存（upsert），返回写入行数。

    同一 ticker 重复写入时会更新 name 与 updated_at，保证幂等。

    Args:
        conn: SQLite 连接。
        rows: ``(ticker, name)`` 列表。

    Returns:
        实际写入（含更新）的行数。
    """
    if not rows:
        return 0
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.executemany(
        f"""
        INSERT INTO {STOCK_NAME_TABLE} (ticker, name, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(ticker) DO UPDATE SET
            name = excluded.name,
            updated_at = excluded.updated_at
        """,
        [(ticker, name, now) for ticker, name in rows],
    )
    conn.commit()
    return len(rows)


def get_missing_name_tickers(
    conn: sqlite3.Connection, tickers: list[str]
) -> list[str]:
    """返回给定 ticker 列表中**本地缓存尚未命中**的 ticker。

    用于每日流程判定“哪些票还需要补名称”，仅补缺失，不全量重拉。

    Args:
        conn: SQLite 连接。
        tickers: 候选 ticker 列表。

    Returns:
        未在缓存中命中的 ticker 列表（保持输入顺序，已去重）。
    """
    cached = batch_get_cached_stock_names(conn, tickers)
    seen: set[str] = set()
    missing: list[str] = []
    for ticker in tickers:
        if ticker in seen:
            continue
        seen.add(ticker)
        if ticker not in cached:
            missing.append(ticker)
    return missing


def refresh_missing_stock_names(
    conn: sqlite3.Connection,
    tickers: list[str],
    query_name: Callable[[str], str | None],
) -> dict[str, str]:
    """对给定 ticker 列表中本地缓存缺失的项，逐只查名称并写入 DB。

    已命中缓存的 ticker 不再联网，仅补缺失，实现「每日仅补漏」。

    Args:
        conn: SQLite 连接。
        tickers: 需要确保名称的 ticker 列表（带前缀）。
        query_name: 单只查名称的可调用，输入 ticker 返回 name 或 None；
                    查询失败/超时应返回 None。

    Returns:
        {ticker: name} 本次新补成功的映射。
    """
    ensure_stock_name_table(conn)
    missing = get_missing_name_tickers(conn, tickers)
    if not missing:
        logger.debug("📛 股票名称缓存已完整，无需补漏")
        return {}

    newly: dict[str, str] = {}
    for ticker in missing:
        try:
            name = query_name(ticker)
        except Exception as exc:  # noqa: BLE001 — 单只查询失败不应中断整批
            logger.debug(f"{ticker} 名称查询异常: {exc}")
            name = None
        if name:
            batch_write_stock_name_cache(conn, [(ticker, name)])
            newly[ticker] = name
            logger.debug(f"✅ 名称补漏: {ticker} → {name}")
        else:
            logger.debug(f"⚠️ {ticker} 名称查询返回空，暂不写入（下次继续补漏）")
    if newly:
        logger.info(
            f"📛 股票名称补漏完成：新增 {len(newly)} 条，缓存已命中 {len(tickers) - len(missing)} 条"
        )
    return newly


def seed_stock_name_cache_from_map(
    conn: sqlite3.Connection, name_map: dict[str, str]
) -> int:
    """把批量获取的名称映射（如 akshare 全市场快照）种入名称缓存。

    仅写入 name 非空的条目，幂等 upsert，用作首次全量种子，后续由
    :func:`refresh_missing_stock_names` 持续补漏。

    Args:
        conn: SQLite 连接。
        name_map: {ticker: name} 映射（ticker 带前缀，如 ``sh.600519``）。

    Returns:
        写入行数。
    """
    ensure_stock_name_table(conn)
    rows = [(ticker, name) for ticker, name in name_map.items() if name]
    return batch_write_stock_name_cache(conn, rows)


def open_name_cache() -> sqlite3.Connection:
    """打开（并建表）名称缓存数据库连接，供调用方使用。

    调用方用完后应自行 ``close()``。

    Returns:
        已打开并建好表的 SQLite 连接。
    """
    conn = get_sqlite_conn(CACHE_DB)
    ensure_stock_name_table(conn)
    return conn


def resolve_stock_names(
    conn: sqlite3.Connection,
    tickers: list[str],
    query_name: Callable[[str], str | None] | None = None,
) -> dict[str, str]:
    """批量解析股票名称，优先本地缓存，缺失补漏。

    实现「命中即本地使用，仅补缺失」：
    - 先一次性从 ``stock_name_cache`` 批量读取已缓存的名称（命中不联网）
    - 对缺失的 ticker，若提供了 ``query_name``，逐只查询并写回 DB
    - 返回仅含有名称的 {ticker: name} 映射

    Args:
        conn: SQLite 连接。
        tickers: 带交易所前缀的 ticker 列表，如 ``["sh.600519", "sz.000858"]``。
        query_name: 单只查名称的可调用，输入 ticker 返回 name 或 None；
                    查询失败/超时应返回 None。缺省时仅返回已缓存项。

    Returns:
        {ticker: name} 映射（仅返回有名称的 ticker）。
    """
    ensure_stock_name_table(conn)
    cached = batch_get_cached_stock_names(conn, tickers)
    missing = [t for t in tickers if t not in cached]
    if missing and query_name is not None:
        newly = refresh_missing_stock_names(conn, missing, query_name)
        cached.update(newly)
    return {t: cached[t] for t in tickers if t in cached and cached[t]}

