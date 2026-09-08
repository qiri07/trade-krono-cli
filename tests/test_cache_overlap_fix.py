#!/usr/bin/env python3
"""trade_krono_cli.cache 增强测试（覆盖 DELETE 逻辑修复）。"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd

from trade_krono_cli.cache import _KLINE_HISTORICAL_TTL, Cache


class TestCacheOverlapFix:
    """测试 K 线缓存重叠修复后的行为。"""

    def test_overlapping_records_deleted(self, tmp_path: Path) -> None:
        """新写入应删除有重叠的旧记录。"""
        db = tmp_path / "test.db"
        cache = Cache(db_path=db)

        # 写入第一条记录
        df1 = pd.DataFrame({"timestamps": ["2022-01-01", "2022-01-02"]})
        cache.set_kline(
            "sh.600519", "2022-01-01", "2022-01-02", "d", df1, ttl=_KLINE_HISTORICAL_TTL
        )

        # 写入有重叠的第二条记录
        df2 = pd.DataFrame({"timestamps": ["2022-01-02", "2022-01-03"]})
        cache.set_kline(
            "sh.600519", "2022-01-02", "2022-01-03", "d", df2, ttl=_KLINE_HISTORICAL_TTL
        )

        # 验证：应该只有一条记录（重叠的被删除）
        conn = sqlite3.connect(str(db))
        count = conn.execute(
            "SELECT COUNT(*) FROM kline_cache WHERE ticker=?", ("sh.600519",)
        ).fetchone()[0]
        conn.close()

        assert count == 1, f"Expected 1 record, got {count}"

    def test_non_overlapping_records_kept(self, tmp_path: Path) -> None:
        """无重叠的记录应保留。"""
        db = tmp_path / "test.db"
        cache = Cache(db_path=db)

        # 写入第一条记录
        df1 = pd.DataFrame({"timestamps": ["2022-01-01", "2022-01-02"]})
        cache.set_kline(
            "sh.600519", "2022-01-01", "2022-01-02", "d", df1, ttl=_KLINE_HISTORICAL_TTL
        )

        # 写入无重叠的第二条记录
        df2 = pd.DataFrame({"timestamps": ["2022-01-03", "2022-01-04"]})
        cache.set_kline(
            "sh.600519", "2022-01-03", "2022-01-04", "d", df2, ttl=_KLINE_HISTORICAL_TTL
        )

        # 验证：应该有两条记录
        conn = sqlite3.connect(str(db))
        count = conn.execute(
            "SELECT COUNT(*) FROM kline_cache WHERE ticker=?", ("sh.600519",)
        ).fetchone()[0]
        conn.close()

        assert count == 2, f"Expected 2 records, got {count}"

    def test_contiguous_records_merged(self, tmp_path: Path) -> None:
        """相邻记录应合并为一条。"""
        db = tmp_path / "test.db"
        cache = Cache(db_path=db)

        # 写入第一条记录
        df1 = pd.DataFrame({"timestamps": ["2022-01-01", "2022-01-02"]})
        cache.set_kline(
            "sh.600519", "2022-01-01", "2022-01-02", "d", df1, ttl=_KLINE_HISTORICAL_TTL
        )

        # 写入相邻的第二条记录
        df2 = pd.DataFrame({"timestamps": ["2022-01-03", "2022-01-04"]})
        cache.set_kline(
            "sh.600519", "2022-01-03", "2022-01-04", "d", df2, ttl=_KLINE_HISTORICAL_TTL
        )

        # 写入扩展第三条记录（覆盖前两条）
        df3 = pd.DataFrame({"timestamps": ["2022-01-01", "2022-01-02", "2022-01-03", "2022-01-04"]})
        cache.set_kline(
            "sh.600519", "2022-01-01", "2022-01-04", "d", df3, ttl=_KLINE_HISTORICAL_TTL
        )

        # 验证：应该只有一条记录
        conn = sqlite3.connect(str(db))
        count = conn.execute(
            "SELECT COUNT(*) FROM kline_cache WHERE ticker=?", ("sh.600519",)
        ).fetchone()[0]
        conn.close()

        assert count == 1, f"Expected 1 record, got {count}"

    def test_partial_overlap_deletes_only_overlapping(self, tmp_path: Path) -> None:
        """部分重叠时只删除重叠部分。"""
        db = tmp_path / "test.db"
        cache = Cache(db_path=db)

        # 写入两条不重叠的记录
        df1 = pd.DataFrame({"timestamps": ["2022-01-01", "2022-01-02"]})
        cache.set_kline(
            "sh.600519", "2022-01-01", "2022-01-02", "d", df1, ttl=_KLINE_HISTORICAL_TTL
        )

        df2 = pd.DataFrame({"timestamps": ["2022-01-05", "2022-01-06"]})
        cache.set_kline(
            "sh.600519", "2022-01-05", "2022-01-06", "d", df2, ttl=_KLINE_HISTORICAL_TTL
        )

        # 写入与第一条部分重叠的记录
        df3 = pd.DataFrame({"timestamps": ["2022-01-02", "2022-01-03", "2022-01-04"]})
        cache.set_kline(
            "sh.600519", "2022-01-02", "2022-01-04", "d", df3, ttl=_KLINE_HISTORICAL_TTL
        )

        # 验证：应该有三条记录（第一条被删除，第二、三条保留）
        conn = sqlite3.connect(str(db))
        rows = conn.execute(
            "SELECT start, end FROM kline_cache WHERE ticker=? ORDER BY start", ("sh.600519",)
        ).fetchall()
        conn.close()

        assert len(rows) == 2, f"Expected 2 records, got {len(rows)}"
        assert rows[0] == ("2022-01-05", "2022-01-06")
        assert rows[1] == ("2022-01-02", "2022-01-04")


class TestCacheTTL:
    """测试缓存 TTL 逻辑。"""

    def test_permanent_cache_ttl_zero(self, tmp_path: Path) -> None:
        """永久缓存 ttl=0。"""
        db = tmp_path / "test.db"
        cache = Cache(db_path=db)

        df = pd.DataFrame({"timestamps": ["2022-01-01"]})
        cache.set_kline("sh.600519", "2022-01-01", "2022-01-01", "d", df, ttl=0.0)

        conn = sqlite3.connect(str(db))
        ttl = conn.execute("SELECT ttl FROM kline_cache WHERE ticker=?", ("sh.600519",)).fetchone()[
            0
        ]
        conn.close()

        assert ttl == 0.0

    def test_temporary_cache_expiry(self, tmp_path: Path) -> None:
        """临时缓存过期后返回 None。"""
        db = tmp_path / "test.db"
        cache = Cache(db_path=db)

        cache.set_ta("sh.600519", "2022-01-01", {"signal": "buy"}, ttl=0.0001)  # 极短 TTL

        time.sleep(0.01)  # 等待过期

        result = cache.get_ta("sh.600519", "2022-01-01")
        assert result is None


class TestCacheTransaction:
    """测试缓存事务处理。"""

    def test_transaction_rollback_on_error(self, tmp_path: Path) -> None:
        """事务出错时回滚。"""
        db = tmp_path / "test.db"
        cache = Cache(db_path=db)

        df = pd.DataFrame({"timestamps": ["2022-01-01"]})
        cache.set_kline("sh.600519", "2022-01-01", "2022-01-01", "d", df, ttl=_KLINE_HISTORICAL_TTL)

        # 验证数据已写入
        conn = sqlite3.connect(str(db))
        count = conn.execute(
            "SELECT COUNT(*) FROM kline_cache WHERE ticker=?", ("sh.600519",)
        ).fetchone()[0]
        conn.close()

        assert count == 1


class TestCacheWarmHistory:
    """测试缓存预热功能。"""

    @patch("trade_krono_cli.cache.fetch_kline")
    def test_warm_history_creates_permanent_cache(
        self, mock_fetch: MagicMock, tmp_path: Path
    ) -> None:
        """预热创建永久缓存。"""
        db = tmp_path / "test.db"
        cache = Cache(db_path=db)

        mock_df = pd.DataFrame(
            {
                "timestamps": pd.date_range("2022-01-01", periods=100, freq="D"),
                "open": [1.0] * 100,
                "high": [1.1] * 100,
                "low": [0.9] * 100,
                "close": [1.0] * 100,
                "volume": [1000] * 100,
            }
        )
        mock_fetch.return_value = mock_df

        fetched, segments = cache.warm_history("sh.600519", "2022-04-10", lookback_days=90)

        assert fetched == 100
        assert segments == 1

        # 验证缓存中存在记录
        conn = sqlite3.connect(str(db))
        count = conn.execute(
            "SELECT COUNT(*) FROM kline_cache WHERE ticker=?", ("sh.600519",)
        ).fetchone()[0]
        conn.close()

        assert count == 1
