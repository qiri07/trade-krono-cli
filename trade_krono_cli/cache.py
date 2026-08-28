"""
缓存层 — TTL 驱动的 SQLite 缓存。

支持三种缓存类型：
  kline_cache  — K 线数据（pd.DataFrame pickle）
  ta_cache     — TA 分析结果（JSON dict）
  kronos_cache — Kronos 预测结果（JSON dict）

缓存语义：
  · K 线：历史数据（>30天前）永久缓存，当日/近期数据 1h TTL
  · TA / Kronos：缓存 key 含 config_hash + 模型版本，配置变更自动失效
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

from trade_krono_cli.config import Settings, get_settings

# Whitelist of allowed cache table names
_CACHE_TABLES: frozenset[str] = frozenset({"kline_cache", "ta_cache", "kronos_cache"})

# K 线 TTL 常量
_KLINE_HISTORICAL_TTL = 0.0  # 0 = 永久（历史数据只追加不失效）
_KLINE_RECENT_TTL = 3600.0  # 1 小时（当日/近期数据）
_KLINE_HISTORY_WINDOW_DAYS = 30  # 超过此天数的数据视为"历史"


def _validate_table_name(table: str, allowed: frozenset[str]) -> str:
    if table not in allowed:
        raise ValueError(f"Unauthorized table: {table}")
    return table


class Cache:
    """SQLite 缓存，支持 K 线、TA 结果、Kronos 预测三种类型。"""

    def __init__(self, db_path: Optional[Path] = None, settings: Optional[Settings] = None):
        self._db_path = db_path or ((settings or get_settings()).cache_dir / "pipeline_cache.db")
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @property
    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, check_same_thread=False, timeout=10.0)
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
                    ticker       TEXT NOT NULL,
                    date         TEXT NOT NULL,
                    config_hash  TEXT NOT NULL DEFAULT '',
                    prompt_ver   TEXT NOT NULL DEFAULT '',
                    model_ver    TEXT NOT NULL DEFAULT '',
                    ttl          REAL NOT NULL,
                    data         BLOB NOT NULL,
                    created      REAL NOT NULL,
                    PRIMARY KEY (ticker, date, config_hash, prompt_ver, model_ver)
                );

                CREATE TABLE IF NOT EXISTS kronos_cache (
                    ticker       TEXT NOT NULL,
                    date         TEXT NOT NULL,
                    pred_len     INTEGER NOT NULL,
                    sample_cnt   INTEGER NOT NULL DEFAULT 1,
                    config_hash  TEXT NOT NULL DEFAULT '',
                    model_ver    TEXT NOT NULL DEFAULT '',
                    ttl          REAL NOT NULL,
                    data         BLOB NOT NULL,
                    created      REAL NOT NULL,
                    PRIMARY KEY (ticker, date, pred_len, sample_cnt, config_hash, model_ver)
                );
            """)
            # 迁移：为旧表添加新列
            for col_sql in [
                "ALTER TABLE ta_cache ADD COLUMN config_hash  TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE ta_cache ADD COLUMN prompt_ver   TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE ta_cache ADD COLUMN model_ver    TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE kronos_cache ADD COLUMN config_hash TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE kronos_cache ADD COLUMN model_ver   TEXT NOT NULL DEFAULT ''",
            ]:
                try:
                    conn.execute(col_sql)
                    logger.debug("📦 缓存表迁移: 新增列")
                except sqlite3.OperationalError:
                    pass

    # ── K 线缓存 ──────────────────────────────────────

    def get_kline(self, ticker: str, start: str, end: str, freq: str) -> Optional[pd.DataFrame]:
        with self._conn as conn:
            row = conn.execute(
                "SELECT data, created, ttl FROM kline_cache "
                "WHERE ticker=? AND start=? AND end=? AND freq=?",
                (ticker, start, end, freq),
            ).fetchone()
        if row is None:
            return None
        data, created, ttl = row
        if ttl < 0 or (ttl > 0 and time.time() - created > ttl):
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
        with self._conn as conn:
            conn.execute(
                "INSERT OR REPLACE INTO kline_cache "
                "(ticker, start, end, freq, ttl, data, created) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ticker, start, end, freq, ttl, buf.read(), time.time()),
            )
            conn.commit()

    def warm_history(self, ticker: str, end_date: str, lookback_days: int = 730) -> tuple[int, int]:
        """预热 K 线缓存：拉取历史数据，历史段永久缓存，近期段 1h TTL。"""
        from datetime import datetime, timedelta

        from trade_krono_cli.data import fetch_kline

        end = datetime.strptime(end_date, "%Y-%m-%d")
        start = end - timedelta(days=lookback_days)
        start_s = start.strftime("%Y-%m-%d")
        recent_cutoff = (end - timedelta(days=_KLINE_HISTORY_WINDOW_DAYS)).strftime("%Y-%m-%d")

        logger.info(
            f"🔥 预热 K 线缓存: {ticker} {start_s}~{end_date} "
            f"(历史={_KLINE_HISTORY_WINDOW_DAYS}d 永久，近期 1h)"
        )
        df = fetch_kline(ticker, start_s, end_date, frequency="d", adjustflag="1", use_cache=True)
        if df is None or len(df) == 0:
            return 0, 0

        fetched = len(df)
        cached = 0
        # 历史段：[start_s, recent_cutoff) → 永久缓存；近期段：[recent_cutoff, end] → 1h TTL
        hist_mask = pd.to_datetime(df["timestamps"]) < pd.Timestamp(recent_cutoff)
        recent_mask = ~hist_mask
        for mask, ttl in [(hist_mask, _KLINE_HISTORICAL_TTL), (recent_mask, _KLINE_RECENT_TTL)]:
            seg = df[mask]
            if len(seg) == 0:
                continue
            seg_start = seg["timestamps"].iloc[0].strftime("%Y-%m-%d")
            seg_end = seg["timestamps"].iloc[-1].strftime("%Y-%m-%d")
            self.set_kline(ticker, seg_start, seg_end, "d", seg, ttl=ttl)
            cached += 1
            logger.debug(f"  📦 {'永久' if ttl == 0 else '1h'}: {ticker} {seg_start}~{seg_end}")

        logger.info(f"✅ K 线缓存预热完成: {ticker} {fetched}行 → {cached}段")
        return fetched, cached

    # ── TA 缓存 ───────────────────────────────────────

    def get_ta(
        self,
        ticker: str,
        date: str,
        config_hash: str = "",
        prompt_ver: str = "",
        model_ver: str = "",
    ) -> Optional[dict]:
        with self._conn as conn:
            row = conn.execute(
                "SELECT data, created, ttl FROM ta_cache "
                "WHERE ticker=? AND date=? AND config_hash=? AND prompt_ver=? AND model_ver=?",
                (ticker, date, config_hash, prompt_ver, model_ver),
            ).fetchone()
        if row is None:
            return None
        data, created, ttl = row
        if ttl < 0 or (ttl > 0 and time.time() - created > ttl):
            return None
        return json.loads(data)

    def set_ta(
        self,
        ticker: str,
        date: str,
        result: dict,
        config_hash: str = "",
        prompt_ver: str = "",
        model_ver: str = "",
        ttl: float = 86400,
    ) -> None:
        with self._conn as conn:
            conn.execute(
                "INSERT OR REPLACE INTO ta_cache "
                "(ticker, date, config_hash, prompt_ver, model_ver, ttl, data, created) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    ticker,
                    date,
                    config_hash,
                    prompt_ver,
                    model_ver,
                    ttl,
                    json.dumps(result, ensure_ascii=False).encode(),
                    time.time(),
                ),
            )
            conn.commit()

    # ── Kronos 缓存 ───────────────────────────────────

    def get_kronos(
        self,
        ticker: str,
        date: str,
        pred_len: int,
        sample_count: int = 1,
        config_hash: str = "",
        model_ver: str = "",
    ) -> Optional[dict]:
        with self._conn as conn:
            row = conn.execute(
                "SELECT data, created, ttl FROM kronos_cache "
                "WHERE ticker=? AND date=? AND pred_len=? AND sample_cnt=? "
                "AND config_hash=? AND model_ver=?",
                (ticker, date, pred_len, sample_count, config_hash, model_ver),
            ).fetchone()
        if row is None:
            return None
        data, created, ttl = row
        if ttl < 0 or (ttl > 0 and time.time() - created > ttl):
            return None
        return json.loads(data)

    def set_kronos(
        self,
        ticker: str,
        date: str,
        pred_len: int,
        result: dict,
        ttl: float = 86400,
        sample_count: int = 1,
        config_hash: str = "",
        model_ver: str = "",
    ) -> None:
        with self._conn as conn:
            conn.execute(
                "INSERT OR REPLACE INTO kronos_cache "
                "(ticker, date, pred_len, sample_cnt, config_hash, model_ver, ttl, data, created) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    ticker,
                    date,
                    pred_len,
                    sample_count,
                    config_hash,
                    model_ver,
                    ttl,
                    json.dumps(result, ensure_ascii=False).encode(),
                    time.time(),
                ),
            )
            conn.commit()

    # ── 工具方法 ──────────────────────────────────────

    def get_cached_date_range(self, ticker: str, freq: str = "d") -> Optional[tuple[str, str]]:
        """
        查询某只股票的已有 K 线缓存覆盖的日期范围。

        该Ticker 的所有缓存条目会被合并成一个连续的 [start, end] 区间。
        若没有缓存或缓存已过期，返回 None。

        Returns
        -------
        (start_date, end_date) 或 None
        """
        with self._conn as conn:
            rows = conn.execute(
                "SELECT start, end, created, ttl FROM kline_cache WHERE ticker=? AND freq=?",
                (ticker, freq),
            ).fetchall()

        if not rows:
            return None

        # 过滤掉已 TTL 过期的条目
        now = time.time()
        valid: list[tuple[str, str]] = []
        for start_s, end_s, created, ttl in rows:
            if ttl >= 0 and (ttl > 0 and now - created > ttl):
                continue
            valid.append((start_s, end_s))

        if not valid:
            return None

        # 合并连续区间：取最早 start 和最早 end
        # （K 线缓存通常是连续段，此处简化为直接取 min/max）
        start_s = min(r[0] for r in valid)
        end_s = max(r[1] for r in valid)
        return (start_s, end_s)

    def clear_all(self) -> int:
        with self._conn as conn:
            count = 0
            for table in _CACHE_TABLES:
                r = conn.execute(
                    f"DELETE FROM {_validate_table_name(table, _CACHE_TABLES)}"
                ).rowcount
                count += r
            conn.commit()
        logger.info(f"🧹 清除缓存 {count} 条（research 数据不受影响）")
        return count

    def stats(self) -> dict:
        with self._conn as conn:
            return {
                f"cache_{t}": conn.execute(
                    f"SELECT COUNT(*) FROM {_validate_table_name(t, _CACHE_TABLES)}"
                ).fetchone()[0]
                for t in _CACHE_TABLES
            }


_cache: Optional[Cache] = None


def get_cache() -> Cache:
    global _cache
    if _cache is None:
        _cache = Cache()
    return _cache


def clear_cache_singleton() -> None:
    global _cache
    _cache = None
