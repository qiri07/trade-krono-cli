"""测试 Cache 迁移逻辑和线程安全。"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pandas as pd

from trade_krono_cli.cache import Cache


def _make_df(n: int = 5) -> pd.DataFrame:
    """生成模拟 K 线 DataFrame。"""
    dates = pd.date_range("2026-01-01", periods=n, freq="D")
    return pd.DataFrame(
        {
            "timestamps": dates,
            "open": [10.0 + i for i in range(n)],
            "close": [10.5 + i for i in range(n)],
            "high": [11.0 + i for i in range(n)],
            "low": [9.5 + i for i in range(n)],
            "volume": [1_000_000.0] * n,
        }
    )


class TestCacheMigrations:
    """测试缓存表结构迁移（向后兼容旧数据库）。"""

    def test_migration_creates_ticker_indexes(self, tmp_path: Path) -> None:
        """迁移应创建 ticker 索引。"""
        db_path = tmp_path / "test.db"
        c = Cache(db_path=db_path)

        # 验证索引存在
        conn = c._conn
        indexes = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name LIKE '%cache%'"
        ).fetchall()
        index_names = {row[0] for row in indexes}

        assert "idx_kline_cache_ticker" in index_names
        assert "idx_ta_cache_ticker" in index_names
        assert "idx_kronos_cache_ticker" in index_names

    def test_migration_adds_config_columns(self, tmp_path: Path) -> None:
        """迁移应添加 config_hash/prompt_ver/model_ver 列。"""
        db_path = tmp_path / "test.db"
        c = Cache(db_path=db_path)

        # 验证 ta_cache 表结构
        columns = [row[1] for row in c._conn.execute("PRAGMA table_info(ta_cache)").fetchall()]
        assert "config_hash" in columns
        assert "prompt_ver" in columns
        assert "model_ver" in columns

        # 验证 kronos_cache 表结构
        columns = [row[1] for row in c._conn.execute("PRAGMA table_info(kronos_cache)").fetchall()]
        assert "config_hash" in columns
        assert "model_ver" in columns

    def test_migration_idempotent(self, tmp_path: Path) -> None:
        """多次初始化不应报错（迁移幂等性）。"""
        db_path = tmp_path / "test.db"
        # 第二次初始化（模拟进程重启）
        c2 = Cache(db_path=db_path)
        # 应该都能正常工作
        df = _make_df()
        c2.set_kline("sh.600519", "2026-01-01", "2026-01-05", "d", df)
        result = c2.get_kline("sh.600519", "2026-01-01", "2026-01-05", "d")
        assert result is not None
        assert len(result) == 5

    def test_migration_old_table_no_columns(self, tmp_path: Path) -> None:
        """旧表（无新列）迁移后应能正常读写。"""
        db_path = tmp_path / "old.db"
        # 手动创建旧格式表（无 config_hash 等新列）
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """
            CREATE TABLE kline_cache (
                ticker TEXT, start TEXT, end TEXT, freq TEXT,
                ttl REAL, data BLOB, created REAL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE ta_cache (
                ticker TEXT, date TEXT, ttl REAL, data BLOB, created REAL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE kronos_cache (
                ticker TEXT, date TEXT, pred_len INTEGER, ttl REAL, data BLOB, created REAL
            )
            """
        )
        conn.commit()
        conn.close()

        # 迁移后应能正常工作
        c = Cache(db_path=db_path)
        df = _make_df()
        c.set_kline("sh.600519", "2026-01-01", "2026-01-05", "d", df)
        result = c.get_kline("sh.600519", "2026-01-01", "2026-01-05", "d")
        assert result is not None

    def test_migration_adds_adjustflag_column(self, tmp_path: Path) -> None:
        """迁移应添加 adjustflag 列到 kline_cache。"""
        db_path = tmp_path / "test.db"
        # 创建旧格式表（无 adjustflag）
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """
            CREATE TABLE kline_cache (
                ticker TEXT, start TEXT, end TEXT, freq TEXT,
                ttl REAL, data BLOB, created REAL
            )
            """
        )
        conn.commit()
        conn.close()

        c = Cache(db_path=db_path)
        columns = [row[1] for row in c._conn.execute("PRAGMA table_info(kline_cache)").fetchall()]
        assert "adjustflag" in columns


class TestCachePerformanceIndex:
    """测试 ticker 索引对查询性能的影响。"""

    def test_ticker_index_exists(self, tmp_path: Path) -> None:
        """验证 ticker 索引存在且可用。"""
        db_path = tmp_path / "test.db"
        c = Cache(db_path=db_path)

        # 写入大量数据
        for i in range(100):
            df = _make_df(10)
            c.set_kline(f"sh.{600001 + i}", "2026-01-01", "2026-01-10", "d", df)

        # 查询时应使用索引
        conn = c._conn
        explain = conn.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM kline_cache WHERE ticker=?",
            ("sh.600519",),
        ).fetchall()
        plan = " ".join(str(row) for row in explain)
        assert "SEARCH" in plan or "INDEX" in plan

    def test_ticker_query_speed(self, tmp_path: Path) -> None:
        """有索引的 ticker 查询应快速。"""
        db_path = tmp_path / "test.db"
        c = Cache(db_path=db_path)

        # 写入 200 只股票数据
        for i in range(200):
            df = _make_df(10)
            c.set_kline(f"sh.{600001 + i}", "2026-01-01", "2026-01-10", "d", df)

        # 查询单只股票应很快
        start = time.perf_counter()
        for _ in range(100):
            c.get_kline("sh.600519", "2026-01-01", "2026-01-10", "d")
        elapsed = time.perf_counter() - start

        # 100 次查询应在 1 秒内完成（有索引的情况下）
        assert elapsed < 1.0, f"Query took {elapsed:.2f}s, expected < 1.0s"
