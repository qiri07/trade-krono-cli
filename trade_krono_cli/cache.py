"""
SQLite 缓存层 — K 线 / TA 结果 / Kronos 预测缓存。
"""
from __future__ import annotations

import json
import sqlite3
import time
from io import BytesIO
from pathlib import Path
from typing import Optional

import pandas as pd

from loguru import logger
from trade_krono_cli.config import get_settings


class Cache:
    """SQLite 缓存，支持 K 线、TA 结果、Kronos 预测三种类型。"""

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or (
            get_settings().cache_dir / "pipeline_cache.db"
        )
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        """初始化数据库表。"""
        with sqlite3.connect(self._db_path) as conn:
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
                    ticker    TEXT NOT NULL,
                    date      TEXT NOT NULL,
                    pred_len  INTEGER NOT NULL,
                    ttl       REAL NOT NULL,
                    data      BLOB NOT NULL,
                    created   REAL NOT NULL,
                    PRIMARY KEY (ticker, date, pred_len)
                );
            """)

    # ── K 线缓存 ──────────────────────────────────────

    def get_kline(
        self, ticker: str, start: str, end: str, freq: str
    ) -> Optional[pd.DataFrame]:
        key = (ticker, start, end, freq)
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT data, created, ttl FROM kline_cache "
                "WHERE ticker=? AND start=? AND end=? AND freq=?",
                key,
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
        from io import BytesIO
        buf = BytesIO()
        df.to_pickle(buf)
        buf.seek(0)
        created = time.time()
        with sqlite3.connect(self._db_path) as conn:
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
        with sqlite3.connect(self._db_path) as conn:
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
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO ta_cache "
                "(ticker, date, ttl, data, created) "
                "VALUES (?, ?, ?, ?, ?)",
                (ticker, date, ttl, json.dumps(result, ensure_ascii=False).encode(), created),
            )
            conn.commit()

    # ── Kronos 缓存 ───────────────────────────────────

    def get_kronos(
        self, ticker: str, date: str, pred_len: int
    ) -> Optional[dict]:
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT data, created, ttl FROM kronos_cache "
                "WHERE ticker=? AND date=? AND pred_len=?",
                (ticker, date, pred_len),
            ).fetchone()
        if row is None:
            return None
        data, created, ttl = row
        if time.time() - created > ttl:
            return None
        return json.loads(data)

    def set_kronos(
        self, ticker: str, date: str, pred_len: int, result: dict, ttl: float = 86400
    ) -> None:
        created = time.time()
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO kronos_cache "
                "(ticker, date, pred_len, ttl, data, created) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (ticker, date, pred_len, ttl,
                 json.dumps(result, ensure_ascii=False).encode(), created),
            )
            conn.commit()

    # ── 工具方法 ──────────────────────────────────────

    def clear_all(self) -> int:
        """清除所有缓存，返回删除行数。"""
        with sqlite3.connect(self._db_path) as conn:
            count = 0
            for table in ("kline_cache", "ta_cache", "kronos_cache"):
                r = conn.execute(f"DELETE FROM {table}").rowcount
                count += r
            conn.commit()
        logger.info(f"🧹 清除缓存 {count} 条")
        return count

    def stats(self) -> dict:
        """返回各表缓存统计。"""
        with sqlite3.connect(self._db_path) as conn:
            return {
                table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("kline_cache", "ta_cache", "kronos_cache")
            }


# 模块级单例
_cache: Optional[Cache] = None


def get_cache() -> Cache:
    global _cache
    if _cache is None:
        _cache = Cache()
    return _cache
