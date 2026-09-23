"""测试 K 线缓存合并逻辑（cache/kline.py）。"""

from __future__ import annotations

import sqlite3

import pandas as pd
import pytest

from trade_krono_cli.cache import _KLINE_HISTORICAL_TTL, Cache, KlineCache


def _make_df(start: str, end: str) -> pd.DataFrame:
    """生成指定日期范围的模拟 DataFrame。"""
    dates = pd.date_range(start, end, freq="D")
    return pd.DataFrame(
        {
            "timestamps": dates,
            "open": [10.0 + i for i in range(len(dates))],
            "close": [10.5 + i for i in range(len(dates))],
            "high": [11.0 + i for i in range(len(dates))],
            "low": [9.5 + i for i in range(len(dates))],
            "volume": [1_000_000.0] * len(dates),
        },
    )


class TestKlineCacheMergeLogic:
    """K 线缓存合并逻辑测试。"""

    @pytest.fixture
    def cache(self, tmp_path) -> Cache:
        return Cache(db_path=tmp_path / "test.db")

    @pytest.fixture
    def kline_cache(self, cache) -> KlineCache:
        return KlineCache(cache)

    def test_set_and_get(self, kline_cache: KlineCache) -> None:
        df = _make_df("2026-01-01", "2026-01-05")
        kline_cache.set_kline(
            "sh.600519", "2026-01-01", "2026-01-05", "d", df, ttl=_KLINE_HISTORICAL_TTL
        )
        result = kline_cache.get_kline("sh.600519", "2026-01-01", "2026-01-05", "d")
        assert result is not None
        assert len(result) == 5

    def test_get_miss(self, kline_cache: KlineCache) -> None:
        result = kline_cache.get_kline("sh.600519", "2026-01-01", "2026-01-05", "d")
        assert result is None

    def test_overwrite_same_segment(self, kline_cache: KlineCache) -> None:
        """同一 segment 覆盖写入：旧数据被替换。"""
        df1 = _make_df("2026-01-01", "2026-01-05")
        df2 = _make_df("2026-01-01", "2026-01-05")
        df2["close"] = [999.0] * 5  # 不同数据

        kline_cache.set_kline(
            "sh.600519", "2026-01-01", "2026-01-05", "d", df1, ttl=_KLINE_HISTORICAL_TTL
        )
        kline_cache.set_kline(
            "sh.600519", "2026-01-01", "2026-01-05", "d", df2, ttl=_KLINE_HISTORICAL_TTL
        )

        result = kline_cache.get_kline("sh.600519", "2026-01-01", "2026-01-05", "d")
        assert result is not None
        assert len(result) == 5
        assert result["close"].iloc[0] == 999.0

    def test_merge_append_new_end(self, kline_cache: KlineCache) -> None:
        """新段追加到已有段末尾（不重叠）：两行并存，get_cached_date_range 合并显示完整范围。"""
        df1 = _make_df("2026-01-01", "2026-01-10")
        df2 = _make_df("2026-01-12", "2026-01-15")  # 跳过周末，避免重叠

        kline_cache.set_kline(
            "sh.600519", "2026-01-01", "2026-01-10", "d", df1, ttl=_KLINE_HISTORICAL_TTL
        )
        kline_cache.set_kline(
            "sh.600519", "2026-01-12", "2026-01-15", "d", df2, ttl=_KLINE_HISTORICAL_TTL
        )

        # 两段并存（非重叠不删除）
        result = kline_cache.get_cached_date_range("sh.600519")
        assert result == ("2026-01-01", "2026-01-15")
        # 各自精确查询都成功
        r1 = kline_cache.get_kline("sh.600519", "2026-01-01", "2026-01-10", "d")
        r2 = kline_cache.get_kline("sh.600519", "2026-01-12", "2026-01-15", "d")
        assert r1 is not None and len(r1) == 10
        assert r2 is not None and len(r2) == 4

    def test_merge_overlapping_segment(self, kline_cache: KlineCache) -> None:
        """新段与旧段部分重叠（左重叠）：两段的端点扩展后合并为一条。"""
        df1 = _make_df("2026-01-01", "2026-01-10")
        df2 = _make_df("2026-01-08", "2026-01-15")

        kline_cache.set_kline(
            "sh.600519", "2026-01-01", "2026-01-10", "d", df1, ttl=_KLINE_HISTORICAL_TTL
        )
        kline_cache.set_kline(
            "sh.600519", "2026-01-08", "2026-01-15", "d", df2, ttl=_KLINE_HISTORICAL_TTL
        )

        # 两段合并为一条，端点扩展
        rows = kline_cache._cache._conn.execute(
            "SELECT start, end FROM kline_cache WHERE ticker=?", ("sh.600519",)
        ).fetchall()
        assert len(rows) == 1
        assert rows[0] == ("2026-01-01", "2026-01-15")
        result = kline_cache.get_kline("sh.600519", "2026-01-01", "2026-01-15", "d")
        assert result is not None
        assert len(result) == 15

    def test_merge_fully_contained(self, kline_cache: KlineCache) -> None:
        """旧段完全在新段内：旧段被删除，新段保留。"""
        df1 = _make_df("2026-01-05", "2026-01-07")
        df2 = _make_df("2026-01-01", "2026-01-10")

        kline_cache.set_kline(
            "sh.600519", "2026-01-05", "2026-01-07", "d", df1, ttl=_KLINE_HISTORICAL_TTL
        )
        kline_cache.set_kline(
            "sh.600519", "2026-01-01", "2026-01-10", "d", df2, ttl=_KLINE_HISTORICAL_TTL
        )

        rows = kline_cache._cache._conn.execute(
            "SELECT start, end FROM kline_cache WHERE ticker=?", ("sh.600519",)
        ).fetchall()
        assert len(rows) == 1
        assert rows[0] == ("2026-01-01", "2026-01-10")
        result = kline_cache.get_kline("sh.600519", "2026-01-01", "2026-01-10", "d")
        assert result is not None
        assert len(result) == 10
        # 使用新段的 close 值 (10.5, 11.5, ...)
        assert result["close"].iloc[0] == 10.5

    def test_merge_same_start_different_length(self, kline_cache: KlineCache) -> None:
        """起点相同、长度不同：旧段被删除。"""
        df1 = _make_df("2026-01-01", "2026-01-05")
        df2 = _make_df("2026-01-01", "2026-01-10")

        kline_cache.set_kline(
            "sh.600519", "2026-01-01", "2026-01-05", "d", df1, ttl=_KLINE_HISTORICAL_TTL
        )
        kline_cache.set_kline(
            "sh.600519", "2026-01-01", "2026-01-10", "d", df2, ttl=_KLINE_HISTORICAL_TTL
        )

        result = kline_cache.get_kline("sh.600519", "2026-01-01", "2026-01-10", "d")
        assert result is not None
        assert len(result) == 10

    def test_get_cached_date_range(self, kline_cache: KlineCache) -> None:
        df1 = _make_df("2026-01-01", "2026-01-10")
        df2 = _make_df("2026-01-11", "2026-01-15")
        kline_cache.set_kline(
            "sh.600519", "2026-01-01", "2026-01-10", "d", df1, ttl=_KLINE_HISTORICAL_TTL
        )
        kline_cache.set_kline(
            "sh.600519", "2026-01-11", "2026-01-15", "d", df2, ttl=_KLINE_HISTORICAL_TTL
        )

        result = kline_cache.get_cached_date_range("sh.600519")
        assert result == ("2026-01-01", "2026-01-15")

    def test_get_cached_date_range_miss(self, kline_cache: KlineCache) -> None:
        result = kline_cache.get_cached_date_range("sh.600519")
        assert result is None

    def test_get_cached_date_range_ttl_expired(self, kline_cache: KlineCache) -> None:
        """TTL 为 0 的永久缓存不应过期。"""
        df = _make_df("2026-01-01", "2026-01-05")
        kline_cache.set_kline("sh.600519", "2026-01-01", "2026-01-05", "d", df, ttl=0.0)
        import time

        time.sleep(0.1)
        result = kline_cache.get_cached_date_range("sh.600519")
        assert result is not None

    def test_no_duplicate_segments_after_merge(self, kline_cache: KlineCache) -> None:
        """多次合并操作后：最终只剩一条最大段。"""
        df1 = _make_df("2026-01-01", "2026-01-10")
        df2 = _make_df("2026-01-05", "2026-01-20")  # 包含 df1
        df3 = _make_df("2026-01-15", "2026-01-25")  # 与 df2 重叠

        kline_cache.set_kline(
            "sh.600519", "2026-01-01", "2026-01-10", "d", df1, ttl=_KLINE_HISTORICAL_TTL
        )
        kline_cache.set_kline(
            "sh.600519", "2026-01-05", "2026-01-20", "d", df2, ttl=_KLINE_HISTORICAL_TTL
        )
        kline_cache.set_kline(
            "sh.600519", "2026-01-15", "2026-01-25", "d", df3, ttl=_KLINE_HISTORICAL_TTL
        )

        conn: sqlite3.Connection = kline_cache._cache._conn
        rows = conn.execute(
            "SELECT start, end FROM kline_cache WHERE ticker=? AND freq=? AND adjustflag=?",
            ("sh.600519", "d", "1"),
        ).fetchall()
        # 三段全部重叠，合并为一条最大段
        assert len(rows) == 1
        assert rows[0] == ("2026-01-01", "2026-01-25")

    def test_different_ticker_same_range(self, kline_cache: KlineCache) -> None:
        """不同 ticker 的相同日期段互不影响。"""
        df = _make_df("2026-01-01", "2026-01-05")
        kline_cache.set_kline(
            "sh.600519", "2026-01-01", "2026-01-05", "d", df, ttl=_KLINE_HISTORICAL_TTL
        )
        kline_cache.set_kline(
            "sz.000858", "2026-01-01", "2026-01-05", "d", df, ttl=_KLINE_HISTORICAL_TTL
        )

        r1 = kline_cache.get_kline("sh.600519", "2026-01-01", "2026-01-05", "d")
        r2 = kline_cache.get_kline("sz.000858", "2026-01-01", "2026-01-05", "d")
        assert r1 is not None
        assert r2 is not None

    def test_corrupted_data_skipped_during_merge(self, kline_cache: KlineCache) -> None:
        """旧缓存数据损坏时：跳过损坏记录，新数据正常写入，不崩溃。"""

        df1 = _make_df("2026-01-01", "2026-01-05")
        kline_cache.set_kline(
            "sh.600519", "2026-01-01", "2026-01-05", "d", df1, ttl=_KLINE_HISTORICAL_TTL
        )
        # 直接篡改 DB 中的 pickle 数据为无效字节
        conn = kline_cache._cache._conn
        conn.execute(
            "UPDATE kline_cache SET data = ? WHERE ticker = ? AND start = ?",
            (b"THIS_IS_NOT_A_PICKLE", "sh.600519", "2026-01-01"),
        )
        conn.commit()

        # 新数据与损坏数据重叠 → 应跳过损坏数据，只写入新数据
        df2 = _make_df("2026-01-03", "2026-01-10")
        kline_cache.set_kline(
            "sh.600519", "2026-01-03", "2026-01-10", "d", df2, ttl=_KLINE_HISTORICAL_TTL
        )

        result = kline_cache.get_kline("sh.600519", "2026-01-03", "2026-01-10", "d")
        assert result is not None
        assert len(result) == 8  # 2026-01-03 ~ 2026-01-10

    def test_pickle_fallback_valid_bytes(self, kline_cache: KlineCache) -> None:
        """旧缓存使用 pickle.loads 序列化的字节应能通过回退路径加载。"""
        import pickle as pickle_mod

        df = _make_df("2026-01-01", "2026-01-05")
        kline_cache.set_kline(
            "sh.600519", "2026-01-01", "2026-01-05", "d", df, ttl=_KLINE_HISTORICAL_TTL
        )
        # 用 pickle.dumps（而非 pd.to_pickle）序列化，模拟旧格式
        conn = kline_cache._cache._conn
        raw_pickle = pickle_mod.dumps(df)
        conn.execute(
            "UPDATE kline_cache SET data = ? WHERE ticker = ? AND start = ?",
            (raw_pickle, "sh.600519", "2026-01-01"),
        )
        conn.commit()

        # 读取应成功（通过 pickle.loads 回退）
        result = kline_cache.get_kline("sh.600519", "2026-01-01", "2026-01-05", "d")
        assert result is not None
        assert len(result) == 5


class TestKlineCacheDataHash:
    """K 线缓存数据完整性校验（P0 安全修复）。"""

    @pytest.fixture
    def cache(self, tmp_path) -> Cache:
        return Cache(db_path=tmp_path / "test.db")

    @pytest.fixture
    def kline_cache(self, cache) -> KlineCache:
        return KlineCache(cache)

    def test_set_kline_stores_hash(self, kline_cache: KlineCache) -> None:
        """写入时自动计算并存储 SHA-256 hash。"""
        import hashlib

        df = _make_df("2026-01-01", "2026-01-03")
        kline_cache.set_kline("sh.600519", "2026-01-01", "2026-01-03", "d", df, ttl=-1)

        row = kline_cache._cache._conn.execute(
            "SELECT data, data_hash FROM kline_cache WHERE ticker=?",
            ("sh.600519",),
        ).fetchone()
        assert row is not None
        data_bytes, stored_hash = row
        assert stored_hash is not None
        expected = hashlib.sha256(b"TKC1" + data_bytes).hexdigest()
        assert stored_hash == expected

    def test_merge_computes_new_hash(self, kline_cache: KlineCache) -> None:
        """合并区间后，新记录存储正确的 hash。"""
        df1 = _make_df("2026-01-01", "2026-01-03")
        df2 = _make_df("2026-01-04", "2026-01-06")
        kline_cache.set_kline("sh.600519", "2026-01-01", "2026-01-03", "d", df1, ttl=-1)
        kline_cache.set_kline("sh.600519", "2026-01-04", "2026-01-06", "d", df2, ttl=-1)

        # 再次写入重叠区间触发合并
        df3 = _make_df("2026-01-01", "2026-01-06")
        kline_cache.set_kline("sh.600519", "2026-01-01", "2026-01-06", "d", df3, ttl=-1)

        row = kline_cache._cache._conn.execute(
            "SELECT COUNT(*), data_hash FROM kline_cache WHERE ticker=?",
            ("sh.600519",),
        ).fetchone()
        count, hash_val = row
        assert count == 1  # 合并为一条
        assert hash_val is not None  # hash 已计算

    def test_tampered_data_rejected(self, kline_cache: KlineCache) -> None:
        """篡改数据后 hash 校验失败，数据被跳过。"""
        df = _make_df("2026-01-01", "2026-01-05")
        kline_cache.set_kline("sh.600519", "2026-01-01", "2026-01-05", "d", df, ttl=-1)

        # 篡改数据字节
        conn = kline_cache._cache._conn
        conn.execute("UPDATE kline_cache SET data = X'cafebabedeadbeef' WHERE ticker=?",
                     ("sh.600519",))
        conn.commit()

        result = kline_cache.get_kline("sh.600519", "2026-01-01", "2026-01-05", "d")
        assert result is None
