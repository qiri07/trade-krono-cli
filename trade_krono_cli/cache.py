"""
缓存层 — TTL 驱动的 SQLite 缓存。

支持三种缓存类型：
  kline_cache  — K 线数据（pd.DataFrame pickle）
  ta_cache     — TA 分析结果（JSON dict）
  kronos_cache — Kronos 预测结果（JSON dict）

TTL 过期后自动失效，不影响研究数据库。
研究数据库（持久化）见 research_db.py。
"""
from __future__ import annotations

import json
import time
from io import BytesIO
from pathlib import Path
from typing import Optional

import pandas as pd
import sqlite3

from loguru import logger
from trade_krono_cli.config import get_settings, Settings

# Whitelist of allowed cache table names — prevents SQL injection via f-strings
_CACHE_TABLES: frozenset[str] = frozenset({"kline_cache", "ta_cache", "kronos_cache"})


def _validate_table_name(table: str, allowed: frozenset[str]) -> str:
    """Validate a table name against an allowed set. Raises ValueError if invalid."""
    if table not in allowed:
        raise ValueError(f"Unauthorized table: {table}")
    return table


class Cache:
    """SQLite 缓存，支持 K 线、TA 结果、Kronos 预测三种类型。TTL 过期后自动失效。"""

    def __init__(self, db_path: Optional[Path] = None, settings: Optional[Settings] = None):
        self._db_path = db_path or (
            (settings or get_settings()).cache_dir / "pipeline_cache.db"
        )
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @property
    def _conn(self) -> sqlite3.Connection:
        """持久连接（懒初始化），避免每次读写新建连接。"""
        conn = sqlite3.connect(self._db_path, check_same_thread=True)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        with self._conn as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS kline_cache (
                    ticker    TEXT NOT NULL,
                    start     TEXT NOT NULL,
                    end       TEXT NOT NULL,
                    freq      TEXT NOT NULL,
                    ttl       REAL NOT NULL,
                    data      BLOB NOT NULL,
                    created   REAL NOT NULL,
                    PRIMARY KEY (ticker, start, end, freq)
                );

                CREATE TABLE IF NOT EXISTS ta_cache (
                    ticker   TEXT NOT NULL,
                    date     TEXT NOT NULL,
                    ttl      REAL NOT NULL,
                    data     BLOB NOT NULL,
                    created  REAL NOT NULL,
                    PRIMARY KEY (ticker, date)
                );

                CREATE TABLE IF NOT EXISTS kronos_cache (
                    ticker      TEXT NOT NULL,
                    date        TEXT NOT NULL,
                    pred_len    INTEGER NOT NULL,
                    sample_cnt  INTEGER NOT NULL DEFAULT 1,
                    ttl         REAL NOT NULL,
                    data        BLOB NOT NULL,
                    created     REAL NOT NULL,
                    PRIMARY KEY (ticker, date, pred_len, sample_cnt)
                );
            """)

    # ── K 线缓存 ──────────────────────────────────────

    def get_kline(
        self, ticker: str, start: str, end: str, freq: str
    ) -> Optional[pd.DataFrame]:
        with self._conn as conn:
            row = conn.execute(
                "SELECT data, created, ttl FROM kline_cache "
                "WHERE ticker=? AND start=? AND end=? AND freq=?",
                (ticker, start, end, freq),
            ).fetchone()
        if row is None:
            return None
        data, created, ttl = row
        if time.time() - created > ttl:
            return None
        return pd.read_pickle(BytesIO(data))

    def set_kline(
        self,
        ticker: str,
        start: str,
        end: str,
        freq: str,
        df: pd.DataFrame,
        ttl: float = 86400,
    ) -> None:
        buf = BytesIO()
        df.to_pickle(buf)
        buf.seek(0)
        created = time.time()
        with self._conn as conn:
            conn.execute(
                "INSERT OR REPLACE INTO kline_cache "
                "(ticker, start, end, freq, ttl, data, created) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ticker, start, end, freq, ttl, buf.read(), created),
            )
            conn.commit()
        logger.debug(f"📦 K线缓存写入: {ticker} {start}~{end}")

    # ── TA 缓存 ───────────────────────────────────────

    def get_ta(self, ticker: str, date: str) -> Optional[dict]:
        with self._conn as conn:
            row = conn.execute(
                "SELECT data, created, ttl FROM ta_cache "
                "WHERE ticker=? AND date=?",
                (ticker, date),
            ).fetchone()
        if row is None:
            return None
        data, created, ttl = row
        if time.time() - created > ttl:
            return None
        return json.loads(data)

    def set_ta(self, ticker: str, date: str, result: dict, ttl: float = 86400) -> None:
        created = time.time()
        with self._conn as conn:
            conn.execute(
                "INSERT OR REPLACE INTO ta_cache "
                "(ticker, date, ttl, data, created) "
                "VALUES (?, ?, ?, ?, ?)",
                (ticker, date, ttl,
                 json.dumps(result, ensure_ascii=False).encode(), created),
            )
            conn.commit()

    # ── Kronos 缓存 ───────────────────────────────────

    def get_kronos(
        self, ticker: str, date: str, pred_len: int, sample_count: int = 1
    ) -> Optional[dict]:
        with self._conn as conn:
            row = conn.execute(
                "SELECT data, created, ttl FROM kronos_cache "
                "WHERE ticker=? AND date=? AND pred_len=? AND sample_cnt=?",
                (ticker, date, pred_len, sample_count),
            ).fetchone()
        if row is None:
            return None
        data, created, ttl = row
        if time.time() - created > ttl:
            return None
        return json.loads(data)

    def set_kronos(
        self, ticker: str, date: str, pred_len: int,
        result: dict, ttl: float = 86400, sample_count: int = 1,
    ) -> None:
        created = time.time()
        with self._conn as conn:
            conn.execute(
                "INSERT OR REPLACE INTO kronos_cache "
                "(ticker, date, pred_len, sample_cnt, ttl, data, created) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ticker, date, pred_len, sample_count, ttl,
                 json.dumps(result, ensure_ascii=False).encode(), created),
            )
            conn.commit()

    # ── 工具方法 ──────────────────────────────────────

    def clear_all(self) -> int:
        """清除所有缓存表（不影响 research 表）。"""
        with self._conn as conn:
            count = 0
            for table in _CACHE_TABLES:
                validated = _validate_table_name(table, _CACHE_TABLES)
                r = conn.execute(f"DELETE FROM {validated}").rowcount
                count += r
            conn.commit()
        logger.info(f"🧹 清除缓存 {count} 条（research 数据不受影响）")
        return count

    def stats(self) -> dict:
        """返回各缓存表统计。"""
        with self._conn as conn:
            return {
                f"cache_{t}": conn.execute(
                    f"SELECT COUNT(*) FROM {_validate_table_name(t, _CACHE_TABLES)}"
                ).fetchone()[0]
                for t in _CACHE_TABLES
            }


# ═══════════════════════════════════════════════════════
# 模块级单例
# ═══════════════════════════════════════════════════════

_cache: Optional[Cache] = None


def get_cache() -> Cache:
    global _cache
    if _cache is None:
        _cache = Cache()
    return _cache


def clear_cache_singleton() -> None:
    """清除缓存单例，使下一次 get_cache() 重新初始化。用于测试隔离。"""
    global _cache
    _cache = None
