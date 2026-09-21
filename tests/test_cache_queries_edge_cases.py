"""CacheQueries — 边界条件与路径补齐测试。"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pandas as pd
import pytest

from trade_krono_cli.cache.base import Cache
from trade_krono_cli.cache.queries import CacheQueries


@pytest.fixture
def cache_with_multi_kline(tmp_path: Path) -> tuple[Cache, CacheQueries]:
    db_path = tmp_path / "test_multi.db"
    c = Cache(db_path=db_path)
    qc = CacheQueries(c)

    df_a = pd.DataFrame(
        {
            "timestamps": pd.to_datetime(["2026-08-01", "2026-08-02"]),
            "open": [1700.0, 1710.0],
            "high": [1720.0, 1730.0],
            "low": [1690.0, 1700.0],
            "close": [1710.0, 1720.0],
            "volume": [1_000_000.0, 1_100_000.0],
        }
    )
    df_b = pd.DataFrame(
        {
            "timestamps": pd.to_datetime(["2026-08-01", "2026-08-03"]),
            "open": [50.0, 52.0],
            "high": [53.0, 54.0],
            "low": [49.0, 51.0],
            "close": [51.0, 53.0],
            "volume": [500_000.0, 550_000.0],
        }
    )

    def _insert(cache: Cache, ticker: str, df: pd.DataFrame) -> None:
        buf = BytesIO()
        df.to_pickle(buf)
        conn = cache._conn
        conn.execute(
            "INSERT INTO kline_cache (ticker, start, end, freq, ttl, data, created) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ticker, "2026-08-01", "2026-08-03", "d", 0.0, buf.getvalue(), 0.0),
        )
        conn.commit()

    _insert(c, "sh.600519", df_a)
    _insert(c, "sz.000001", df_b)
    return c, qc


class TestCacheQueriesExportDailyPvH5:
    def test_export_with_h5(
        self, cache_with_multi_kline: tuple[Cache, CacheQueries], tmp_path: Path
    ) -> None:
        pytest.importorskip("tables", reason="hdf5 support requires pytables")
        _, qc = cache_with_multi_kline
        parquet_path = str(tmp_path / "out.parquet")
        h5_path = str(tmp_path / "out.h5")
        result = qc.export_daily_pv(parquet_path, h5_path=h5_path)
        assert result["rows"] > 0
        assert Path(parquet_path).exists()
        assert Path(h5_path).exists()
        assert result["h5_path"] == h5_path

    def test_export_with_debug_insts(
        self, cache_with_multi_kline: tuple[Cache, CacheQueries], tmp_path: Path
    ) -> None:
        _, qc = cache_with_multi_kline
        parquet_path = str(tmp_path / "out.parquet")
        result = qc.export_daily_pv(parquet_path, debug_insts=1)
        assert result["rows"] > 0
        assert Path(parquet_path).exists()
        assert "debug_path" in result

    def test_export_debug_h5_skipped_when_no_env(
        self, cache_with_multi_kline: tuple[Cache, CacheQueries], tmp_path: Path
    ) -> None:
        """无 rdagent-env 时跳过 debug h5 生成（不报错）。"""
        _, qc = cache_with_multi_kline
        parquet_path = str(tmp_path / "out.parquet")
        # patch Path.exists 使其对所有路径返回 False
        with pytest.MonkeyPatch().context() as mp:
            from pathlib import Path as P

            mp.setattr(P, "exists", lambda self: False)
            result = qc.export_daily_pv(parquet_path, debug_insts=1)
        assert result["rows"] > 0


class TestCacheQueriesStatsEdgeCases:
    def test_stats_empty_tables(self, tmp_path: Path) -> None:
        c = Cache(db_path=tmp_path / "empty.db")
        qc = CacheQueries(c)
        result = qc.stats()
        assert all(v == 0 for v in result.values())

    def test_stats_multiple_tables(self, tmp_path: Path) -> None:
        c = Cache(db_path=tmp_path / "multi.db")
        qc = CacheQueries(c)
        result = qc.stats()
        assert set(result.keys()) == {"cache_kline_cache", "cache_ta_cache", "cache_kronos_cache"}


class TestCacheQueriesClearAllIdempotent:
    def test_clear_all_twice(self, cache_with_multi_kline: tuple[Cache, CacheQueries]) -> None:
        _, qc = cache_with_multi_kline
        first = qc.clear_all()
        assert first > 0
        second = qc.clear_all()
        assert second == 0

    def test_clear_all_then_stats_zero(
        self, cache_with_multi_kline: tuple[Cache, CacheQueries]
    ) -> None:
        _, qc = cache_with_multi_kline
        qc.clear_all()
        stats = qc.stats()
        assert stats["cache_kline_cache"] == 0
        assert stats["cache_ta_cache"] == 0
        assert stats["cache_kronos_cache"] == 0


class TestCacheQueriesExportDailyPvDedup:
    def test_export_same_ticker_different_ranges(self, tmp_path: Path) -> None:
        """同一 ticker 不同 (start, end) 范围共存，导出合并。"""
        db_path = tmp_path / "dup.db"
        c = Cache(db_path=db_path)
        qc = CacheQueries(c)

        df1 = pd.DataFrame(
            {
                "timestamps": pd.to_datetime(["2026-08-01", "2026-08-02"]),
                "open": [100.0, 102.0],
                "high": [103.0, 104.0],
                "low": [99.0, 101.0],
                "close": [101.0, 103.0],
                "volume": [1e6, 1.1e6],
            }
        )
        df2 = pd.DataFrame(
            {
                "timestamps": pd.to_datetime(["2026-08-03", "2026-08-04"]),
                "open": [104.0, 106.0],
                "high": [107.0, 108.0],
                "low": [103.0, 105.0],
                "close": [105.0, 107.0],
                "volume": [1.2e6, 1.3e6],
            }
        )

        buf1 = BytesIO()
        df1.to_pickle(buf1)
        buf2 = BytesIO()
        df2.to_pickle(buf2)
        conn = c._conn
        conn.execute(
            "INSERT INTO kline_cache (ticker, start, end, freq, ttl, data, created) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("sh.600519", "2026-08-01", "2026-08-02", "d", 0.0, buf1.getvalue(), 0.0),
        )
        conn.execute(
            "INSERT INTO kline_cache (ticker, start, end, freq, ttl, data, created) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("sh.600519", "2026-08-03", "2026-08-04", "d", 0.0, buf2.getvalue(), 0.0),
        )
        conn.commit()

        parquet_path = str(tmp_path / "dedup.parquet")
        result = qc.export_daily_pv(parquet_path)
        # 两个不同的 (start,end) 共 2 * 2 = 4 行
        assert result["rows"] == 4
