"""K 线缓存操作 — get_kline / set_kline / warm_history / get_cached_date_range。"""

from __future__ import annotations

import hashlib
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

# 数据完整性协议版本前缀（写入时拼接到数据头部，读取时验证）
_CACHE_FMT_VERSION = b"TKC1"


def _compute_data_hash(data: bytes) -> str:
    """计算 K 线数据的 SHA-256 校验和（含版本前缀防重放）。"""
    h = hashlib.sha256()
    h.update(_CACHE_FMT_VERSION)
    h.update(data)
    return h.hexdigest()


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
            "SELECT data, created, ttl, data_hash FROM kline_cache "
            "WHERE ticker=? AND freq=? AND adjustflag=? "
            "AND end >= ? AND start <= ?",  # 重叠或相邻：现有end >= 查询start 且 现有start <= 查询end
            (ticker, freq, adjustflag, start, end),
        )
        if not rows:
            return None
        dfs: list[pd.DataFrame] = []
        for data, created, ttl, data_hash in rows:
            if ttl < 0 or (ttl > 0 and time.time() - created > ttl):
                continue
            # 数据完整性校验（仅对新格式数据验证 hash）
            if data_hash is not None:
                expected = _compute_data_hash(data)
                if expected != data_hash:
                    logger.warning(
                        f"⚠️ 缓存数据完整性校验失败 ticker={ticker} "
                        f"start={start} end={end}，数据可能已被篡改或损坏，跳过"
                    )
                    continue
            try:
                dfs.append(pd.read_pickle(BytesIO(data)))
            except (ModuleNotFoundError, AttributeError, TypeError, OSError):
                # pickle.loads 作为回退：仅在数据为合法 pickle 字节时执行
                if isinstance(data, (bytes, bytearray)) and len(data) > 4:
                    # 旧格式无 hash，允许降级读取但记录警告
                    if data_hash is None:
                        logger.warning(
                            f"⚠️ 缓存数据为旧格式（无完整性校验），建议重新写入: {ticker}"
                        )
                    try:
                        dfs.append(pickle.loads(data))
                    except Exception as e2:
                        logger.warning(f"⚠️ pickle 回退解析失败: {e2}")
                else:
                    logger.warning("⚠️ 缓存数据格式异常，跳过")
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
        raw_bytes = buf.read()
        buf.seek(0)
        data_hash = _compute_data_hash(raw_bytes)
        self._cache._transaction(
            lambda conn: self._set_kline(conn, ticker, start, end, freq, raw_bytes, data_hash, ttl, adjustflag)
        )

    def _set_kline(
        self,
        conn: sqlite3.Connection,
        ticker: str,
        start: str,
        end: str,
        freq: str,
        raw_bytes: bytes,
        data_hash: str,
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
            all_dfs.append(pd.read_pickle(BytesIO(raw_bytes)))
            merged = (
                pd.concat(all_dfs)
                .drop_duplicates(subset=["timestamps"], keep="last")
                .sort_values("timestamps")
            )
            merged_buf = BytesIO()
            merged.to_pickle(merged_buf)
            merged_buf.seek(0)
            merged_bytes = merged_buf.read()
            merged_hash = _compute_data_hash(merged_bytes)

            # 删除重叠记录
            conn.executemany(
                "DELETE FROM kline_cache WHERE rowid=?",
                [(r[0],) for r in rows],
            )
            # 插入合并后记录
            conn.execute(
                "INSERT INTO kline_cache "
                "(ticker, start, end, freq, adjustflag, ttl, data, data_hash, created) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    ticker,
                    merged["timestamps"].min().strftime("%Y-%m-%d"),
                    merged["timestamps"].max().strftime("%Y-%m-%d"),
                    freq,
                    adjustflag,
                    ttl,
                    merged_bytes,
                    merged_hash,
                    time.time(),
                ),
            )
        else:
            # 无重叠，直接插入
            conn.execute(
                "INSERT INTO kline_cache "
                "(ticker, start, end, freq, adjustflag, ttl, data, data_hash, created) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ticker, start, end, freq, adjustflag, ttl, raw_bytes, data_hash, time.time()),
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
