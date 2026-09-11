"""测试 CacheQueries — stats / export_daily_pv / clear_all。"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pandas as pd
import pytest

from trade_krono_cli.cache.base import Cache
from trade_krono_cli.cache.queries import CacheQueries
from trade_krono_cli.cache.ta import TADataCache


@pytest.fixture
def cache_with_data(tmp_path: Path) -> tuple[Cache, CacheQueries]:
    """创建一个带测试数据的 Cache 实例。"""
    db_path = tmp_path / "test_cache.db"
    c = Cache(db_path=db_path)
    qc = CacheQueries(c)

    # 写入一条 TA 缓存数据
    ta_data = {"ticker": "sh.600519", "date": "2026-09-01", "signal": "BUY"}
    ta_cache = TADataCache(c)
    ta_cache.set_ta("sh.600519", "2026-09-01", ta_data)

    # 写入一条 K 线缓存数据
    df = pd.DataFrame(
        {
            "timestamps": pd.to_datetime(["2026-08-01", "2026-08-02"]),
            "open": [1700.0, 1710.0],
            "high": [1720.0, 1730.0],
            "low": [1690.0, 1700.0],
            "close": [1710.0, 1720.0],
            "volume": [1_000_000.0, 1_100_000.0],
        }
    )
    buf = BytesIO()
    df.to_pickle(buf)
    conn = c._conn
    conn.execute(
        "INSERT INTO kline_cache (ticker, start, end, freq, ttl, data, created) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("sh.600519", "2026-08-01", "2026-08-02", "d", 0.0, buf.getvalue(), 0.0),
    )
    conn.commit()

    return c, qc


class TestCacheQueriesStats:
    """CacheQueries.stats 测试。"""

    def test_stats_returns_counts(self, cache_with_data: tuple[Cache, CacheQueries]) -> None:
        _, qc = cache_with_data
        result = qc.stats()
        assert "cache_kline_cache" in result
        assert "cache_ta_cache" in result
        assert "cache_kronos_cache" in result
        assert isinstance(result["cache_ta_cache"], int)
        assert result["cache_ta_cache"] >= 1

    def test_stats_empty_cache(self, tmp_path: Path) -> None:
        db_path = tmp_path / "empty_cache.db"
        c = Cache(db_path=db_path)
        qc = CacheQueries(c)
        result = qc.stats()
        assert result["cache_ta_cache"] == 0
        assert result["cache_kline_cache"] == 0
        assert result["cache_kronos_cache"] == 0


class TestCacheQueriesClearAll:
    """CacheQueries.clear_all 测试。"""

    def test_clear_all_removes_all_tables(
        self, cache_with_data: tuple[Cache, CacheQueries]
    ) -> None:
        c, qc = cache_with_data
        count = qc.clear_all()
        assert count >= 1
        stats = qc.stats()
        assert stats["cache_ta_cache"] == 0
        assert stats["cache_kline_cache"] == 0
        assert stats["cache_kronos_cache"] == 0

    def test_clear_all_on_empty_cache(self, tmp_path: Path) -> None:
        db_path = tmp_path / "empty_cache.db"
        c = Cache(db_path=db_path)
        qc = CacheQueries(c)
        count = qc.clear_all()
        assert count == 0


class TestCacheQueriesExportDailyPv:
    """CacheQueries.export_daily_pv 测试。"""

    def test_export_daily_pv_creates_parquet(
        self, cache_with_data: tuple[Cache, CacheQueries], tmp_path: Path
    ) -> None:
        _, qc = cache_with_data
        parquet_path = str(tmp_path / "daily_pv.parquet")
        result = qc.export_daily_pv(parquet_path)

        assert result["rows"] > 0
        assert result["stocks"] >= 1
        assert Path(parquet_path).exists()

        df = pd.read_parquet(parquet_path)
        assert "$open" in df.columns
        assert "$close" in df.columns
        assert "$volume" in df.columns

    def test_export_daily_pv_empty_cache(self, tmp_path: Path) -> None:
        db_path = tmp_path / "empty_cache.db"
        c = Cache(db_path=db_path)
        qc = CacheQueries(c)
        parquet_path = str(tmp_path / "empty.parquet")
        with pytest.raises(ValueError, match="No objects to concatenate"):
            qc.export_daily_pv(parquet_path)
