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
        # 查询所有与目标区间有重叠或相邻的记录
        rows = self._cache._query_all(
            "SELECT data, created, ttl FROM kline_cache "
            "WHERE ticker=? AND freq=? AND adjustflag=? "
            "AND end >= ? AND start <= ?",  # 重叠或相邻：现有end >= 查询start 且 现有start <= 查询end
            (ticker, freq, adjustflag, start, end),
        )
        if not rows:
            return None
        dfs: list[pd.DataFrame] = []
        for data, created, ttl in rows:
            if ttl < 0 or (ttl > 0 and time.time() - created > ttl):
                continue
            try:
                dfs.append(pd.read_pickle(BytesIO(data)))
            except (ModuleNotFoundError, AttributeError, TypeError):
                dfs.append(pickle.loads(data))
        if not dfs:
            return None
        merged = (
            pd.concat(dfs)
            .drop_duplicates(subset=["timestamps"], keep="last")
            .sort_values("timestamps")
        )
        # 裁剪到请求的日期范围
        mask = (merged["timestamps"] >= start) & (merged["timestamps"] <= end)
        return merged.loc[mask].reset_index(drop=True)

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
        # 查找所有与新区间有重叠或相邻的记录（包括端点相接的情况）
        rows = conn.execute(
            "SELECT rowid, data FROM kline_cache "
            "WHERE ticker=? AND freq=? AND adjustflag=? "
            "AND end > ? AND start < ?",  # 相邻或重叠：现有end > 新start 且 现有start < 新end
            (ticker, freq, adjustflag, start, end),
        ).fetchall()

        if rows:
            # 合并所有重叠记录的数据
            all_dfs: list[pd.DataFrame] = []
            for _, old_data in rows:
                try:
                    all_dfs.append(pd.read_pickle(BytesIO(old_data)))
                except Exception as e:
                    logger.warning(f"⚠️ 缓存数据损坏，跳过 ticker={ticker} rowid={rows[0][0]}: {e}")
            all_dfs.append(pd.read_pickle(buf))
            merged = (
                pd.concat(all_dfs)
                .drop_duplicates(subset=["timestamps"], keep="last")
                .sort_values("timestamps")
            )
            merged_buf = BytesIO()
            merged.to_pickle(merged_buf)
            merged_buf.seek(0)

            # 删除重叠记录
            conn.executemany(
                "DELETE FROM kline_cache WHERE rowid=?",
                [(r[0],) for r in rows],
            )
            # 插入合并后记录
            conn.execute(
                "INSERT INTO kline_cache "
                "(ticker, start, end, freq, adjustflag, ttl, data, created) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    ticker,
                    merged["timestamps"].min().strftime("%Y-%m-%d"),
                    merged["timestamps"].max().strftime("%Y-%m-%d"),
                    freq,
                    adjustflag,
                    ttl,
                    merged_buf.read(),
                    time.time(),
                ),
            )
        else:
            # 无重叠，直接插入
            buf.seek(0)
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
