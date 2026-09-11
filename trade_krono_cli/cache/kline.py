"""K 线缓存操作 — get_kline / set_kline / warm_history / get_cached_date_range。"""

from __future__ import annotations

import pickle
import sqlite3
import time
from datetime import datetime, timedelta
from io import BytesIO

import pandas as pd
from loguru import logger

from trade_krono_cli.cache.base import (
    KLINE_HISTORICAL_TTL,
    Cache,
)


class KlineCache:
    """K 线缓存操作集。"""

    def __init__(self, cache: Cache) -> None:
        self._cache = cache

    def get_kline(
        self,
        ticker: str,
        start: str,
        end: str,
        freq: str,
        adjustflag: str = "1",
    ) -> pd.DataFrame | None:
        row = self._cache._query_one(
            "SELECT data, created, ttl FROM kline_cache "
            "WHERE ticker=? AND start=? AND end=? AND freq=? AND adjustflag=?",
            (ticker, start, end, freq, adjustflag),
        )
        if row is None:
            return None
        data, created, ttl = row
        if ttl < 0 or (ttl > 0 and time.time() - created > ttl):
            return None
        try:
            return pd.read_pickle(BytesIO(data))
        except (ModuleNotFoundError, AttributeError, TypeError):
            # pyarrow 未安装或旧版 pandas pickle 兼容回退
            return pickle.loads(data)

    def set_kline(
        self,
        ticker: str,
        start: str,
        end: str,
        freq: str,
        df: pd.DataFrame,
        ttl: float = 86400,
        adjustflag: str = "1",
    ) -> None:
        buf = BytesIO()
        df.to_pickle(buf)
        buf.seek(0)
        self._cache._transaction(
            lambda conn: self._set_kline(conn, ticker, start, end, freq, buf, ttl, adjustflag)
        )

    def _set_kline(
        self,
        conn: sqlite3.Connection,
        ticker: str,
        start: str,
        end: str,
        freq: str,
        buf: BytesIO,
        ttl: float,
        adjustflag: str,
    ) -> None:
        # 历史数据写入策略：删除与新段有实质性重叠的旧段，插入新段
        # 重叠/包含判定（满足任一即删除）：
        #   1. 旧段完全在新段内（含边界相等）
        #   2. 旧段起点等于新段起点（同一位置不同长度）
        conn.execute(
            "DELETE FROM kline_cache "
            "WHERE ticker=? AND freq=? AND adjustflag=? "
            "AND (start > ? AND end < ? OR "
            "     start >= ? AND end <= ? OR "
            "     start = ?)",
            (ticker, freq, adjustflag, start, end, start, end, start),
        )
        conn.execute(
            "INSERT INTO kline_cache "
            "(ticker, start, end, freq, adjustflag, ttl, data, created) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (ticker, start, end, freq, adjustflag, ttl, buf.read(), time.time()),
        )

    def warm_history(self, ticker: str, end_date: str, lookback_days: int = 730) -> tuple[int, int]:
        """预热 K 线缓存：拉取历史数据，全部以永久缓存写入。"""
        from trade_krono_cli.data import fetch_kline

        end = datetime.strptime(end_date, "%Y-%m-%d")
        start = end - timedelta(days=lookback_days)
        start_s = start.strftime("%Y-%m-%d")

        logger.info(f"🔥 预热 K 线缓存: {ticker} {start_s}~{end_date}（全部永久缓存）")
        df = fetch_kline(ticker, start_s, end_date, frequency="d", adjustflag="1", use_cache=True)
        if df is None or len(df) == 0:
            return 0, 0

        fetched = len(df)
        seg_start = df["timestamps"].iloc[0].strftime("%Y-%m-%d")
        seg_end = df["timestamps"].iloc[-1].strftime("%Y-%m-%d")
        self.set_kline(ticker, seg_start, seg_end, "d", df, ttl=KLINE_HISTORICAL_TTL)
        logger.debug(f"  📦 永久缓存: {ticker} {seg_start}~{seg_end}")

        logger.info(f"✅ K 线缓存预热完成: {ticker} {fetched}行 → 1段（永久）")
        return fetched, 1

    def get_cached_date_range(
        self,
        ticker: str,
        freq: str = "d",
        adjustflag: str = "1",
    ) -> tuple[str, str] | None:
        """查询某只股票的已有 K 线缓存覆盖的日期范围。"""
        rows = self._cache._query_all(
            "SELECT start, end, created, ttl FROM kline_cache WHERE ticker=? AND freq=? AND adjustflag=?",
            (ticker, freq, adjustflag),
        )

        if not rows:
            return None

        now = time.time()
        valid: list[tuple[str, str]] = []
        for start_s, end_s, created, ttl in rows:
            if ttl >= 0 and (ttl > 0 and now - created > ttl):
                continue
            valid.append((start_s, end_s))

        if not valid:
            return None

        valid_sorted = sorted(valid, key=lambda r: r[0])
        merged: list[tuple[str, str]] = [valid_sorted[0]]
        for s, e in valid_sorted[1:]:
            if s <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], e))
            else:
                merged.append((s, e))
        return (merged[0][0], merged[-1][1])
