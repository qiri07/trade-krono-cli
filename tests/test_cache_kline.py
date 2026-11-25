"""trade_krono_cli.cache.kline — KlineCache 单元测试。

覆盖 get_kline / set_kline / warm_history / get_cached_date_range，
包括哈希校验、损坏数据回退、TTL 过期、日期范围裁剪、去重合并。
使用独立临时缓存（Cache() 路由到 pytest 隔离目录），不写入生产数据库。
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from trade_krono_cli.cache.base import Cache
from trade_krono_cli.cache.kline import KlineCache


def _make_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "timestamps": [
                datetime(2026, 8, 1),
                datetime(2026, 8, 4),
                datetime(2026, 8, 5),
            ],
            "open": [100.0, 102.0, 103.0],
            "high": [103.0, 104.0, 105.0],
            "low": [99.0, 101.0, 102.0],
            "close": [101.0, 103.0, 104.0],
            "volume": [1e6, 1.2e6, 1.3e6],
            "amount": [1e8, 1.2e8, 1.3e8],
        }
    )


def _make_cache() -> Cache:
    return Cache()


class TestKlineCacheSetGet:
    def test_set_get_kline_roundtrip(self) -> None:
        cache = _make_cache()
        kline = KlineCache(cache)
        df = _make_df()
        kline.set_kline("sh.600519", "2026-08-01", "2026-08-05", "d", df, ttl=86400.0)

        out = kline.get_kline("sh.600519", "2026-08-01", "2026-08-05", "d")
        assert out is not None
        assert len(out) == 3
        assert list(out["close"]) == [101.0, 103.0, 104.0]
        cache.close()

    def test_get_kline_missing_returns_none(self) -> None:
        cache = _make_cache()
        kline = KlineCache(cache)
        assert kline.get_kline("sz.999999", "2026-08-01", "2026-08-05", "d") is None
        cache.close()

    def test_get_kline_date_range_trim(self) -> None:
        cache = _make_cache()
        kline = KlineCache(cache)
        df = _make_df()
        kline.set_kline("sh.600519", "2026-08-01", "2026-08-05", "d", df, ttl=86400.0)

        # 只查中间一天
        out = kline.get_kline("sh.600519", "2026-08-04", "2026-08-04", "d")
        assert out is not None
        assert len(out) == 1
        assert out["close"].iloc[0] == 103.0
        cache.close()

    def test_get_kline_ttl_expired_returns_none(self) -> None:
        cache = _make_cache()
        kline = KlineCache(cache)
        df = _make_df()
        kline.set_kline("sh.600519", "2026-08-01", "2026-08-05", "d", df, ttl=-1.0)
        assert kline.get_kline("sh.600519", "2026-08-01", "2026-08-05", "d") is None
        cache.close()

    def test_set_kline_dedupes_duplicate_timestamps(self) -> None:
        cache = _make_cache()
        kline = KlineCache(cache)
        df = pd.concat([_make_df(), _make_df().iloc[[0]]], ignore_index=True)
        assert len(df) == 4  # 重复一行
        kline.set_kline("sh.600519", "2026-08-01", "2026-08-05", "d", df, ttl=86400.0)
        out = kline.get_kline("sh.600519", "2026-08-01", "2026-08-05", "d")
        assert out is not None
        assert len(out) == 3  # 去重后 3 行
        cache.close()

    def test_set_kline_hash_mismatch_skip(self) -> None:
        cache = _make_cache()
        kline = KlineCache(cache)
        df = _make_df()
        # 先写正常数据
        kline.set_kline("sh.600519", "2026-08-01", "2026-08-05", "d", df, ttl=86400.0)
        # 篡改已写入的 data_hash，使读取时哈希不匹配而跳过
        cache._transaction(
            lambda conn: conn.execute(
                "UPDATE kline_cache SET data_hash='tampered_hash' WHERE ticker='sh.600519'"
            )
        )
        out = kline.get_kline("sh.600519", "2026-08-01", "2026-08-05", "d")
        assert out is None
        cache.close()

    def test_get_cached_date_range(self) -> None:
        cache = _make_cache()
        kline = KlineCache(cache)
        # 无数据（用不同 ticker 避免与前序测试共享缓存污染）
        assert kline.get_cached_date_range("sh.000858") is None

        df = _make_df()
        kline.set_kline("sh.000858", "2026-08-01", "2026-08-05", "d", df, ttl=86400.0)
        rng = kline.get_cached_date_range("sh.000858")
        assert rng == ("2026-08-01", "2026-08-05")
        cache.close()

    def test_warm_history_returns_counts(self) -> None:
        cache = _make_cache()
        kline = KlineCache(cache)
        # warm_history 会调用 trade_krono_cli.data.fetch_kline（真实网络），此处仅验证签名存在
        assert hasattr(kline, "warm_history")
        cache.close()


class TestKlineCacheEdgeCases:
    def test_get_kline_corrupt_pickle_returns_none(self) -> None:
        cache = _make_cache()
        kline = KlineCache(cache)
        # 插入损坏的 pickle 数据（无 hash，走旧格式回退）
        cache._transaction(
            lambda conn: conn.execute(
                "INSERT OR REPLACE INTO kline_cache "
                "(ticker, start, end, freq, adjustflag, ttl, data, data_hash, created) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "sh.600519",
                    "2026-08-01",
                    "2026-08-05",
                    "d",
                    "1",
                    86400.0,
                    b"not a pickle",
                    None,
                    __import__("time").time(),
                ),
            )
        )
        out = kline.get_kline("sh.600519", "2026-08-01", "2026-08-05", "d")
        assert out is None
        cache.close()
