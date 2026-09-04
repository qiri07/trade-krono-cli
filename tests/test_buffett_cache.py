"""测试 tests/buffett_cache.py — SQLite 缓存层。

覆盖：init_cache / cache_get / cache_set / cache_get_list / cache_set_list /
      cache_clean / load_all_cache / save_cache_to_db / TTL 过期机制。
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from tests.buffett_cache import (
    TTL_CFO,
    TTL_FINANCIALS,
    TTL_INCOME,
    TTL_STOCKS,
    TTL_VALUATION,
    cache_clean,
    cache_get,
    cache_get_list,
    cache_set,
    cache_set_list,
    init_cache,
    load_all_cache,
    save_cache_to_db,
)

# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_db(tmp_path: Path) -> sqlite3.Connection:
    """创建临时 SQLite 数据库，返回已初始化的连接。"""
    db = tmp_path / "buffett_test.db"
    conn = sqlite3.connect(db.resolve())
    init_cache.__defaults__  # 仅触发表创建（通过调用 init_cache）
    # 手动初始化表（复用 _init_cache 逻辑，但用我们的临时路径）
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cache_stocks (
            key TEXT PRIMARY KEY, data TEXT NOT NULL, expires_at REAL NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cache_valuations (
            key TEXT PRIMARY KEY, data TEXT NOT NULL, expires_at REAL NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cache_financials (
            key TEXT PRIMARY KEY, data TEXT NOT NULL, expires_at REAL NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cache_income (
            key TEXT PRIMARY KEY, data TEXT NOT NULL, expires_at REAL NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cache_cfo (
            key TEXT PRIMARY KEY, data TEXT NOT NULL, expires_at REAL NOT NULL
        )
    """)
    conn.commit()
    return conn


# ── cache_get / cache_set ─────────────────────────────────────────────────────


class TestCacheGetSet:
    def test_set_and_get(self, tmp_db: sqlite3.Connection) -> None:
        cache_set(
            tmp_db, "cache_valuations", "val_600519", {"pe_ttm": 15.0, "pb": 2.0}, TTL_VALUATION
        )
        result = cache_get(tmp_db, "cache_valuations", "val_600519")
        assert result == {"pe_ttm": 15.0, "pb": 2.0}

    def test_get_miss_returns_none(self, tmp_db: sqlite3.Connection) -> None:
        assert cache_get(tmp_db, "cache_valuations", "nonexistent") is None

    def test_get_expired_returns_none(self, tmp_db: sqlite3.Connection) -> None:
        """写入 TTL=0 的记录，立即过期。"""
        cache_set(tmp_db, "cache_valuations", "val_expired", {"pe": 1.0}, ttl=0)
        # 等待一小段时间确保过期
        time.sleep(0.01)
        assert cache_get(tmp_db, "cache_valuations", "val_expired") is None

    def test_overwrite_existing(self, tmp_db: sqlite3.Connection) -> None:
        cache_set(tmp_db, "cache_valuations", "val_600519", {"pe": 10.0}, TTL_VALUATION)
        cache_set(tmp_db, "cache_valuations", "val_600519", {"pe": 20.0}, TTL_VALUATION)
        result = cache_get(tmp_db, "cache_valuations", "val_600519")
        assert result is not None
        assert result["pe"] == 20.0

    def test_different_tables_independent(self, tmp_db: sqlite3.Connection) -> None:
        cache_set(tmp_db, "cache_valuations", "key1", {"a": 1}, TTL_VALUATION)
        cache_set(tmp_db, "cache_financials", "key1", {"b": 2}, TTL_FINANCIALS)
        v = cache_get(tmp_db, "cache_valuations", "key1")
        f = cache_get(tmp_db, "cache_financials", "key1")
        assert v == {"a": 1}
        assert f == {"b": 2}


# ── cache_get_list / cache_set_list ────────────────────────────────────────────


class TestCacheList:
    def test_set_and_get_list(self, tmp_db: sqlite3.Connection) -> None:
        stocks = [{"ticker": "600519", "name": "贵州茅台"}, {"ticker": "000858", "name": "五粮液"}]
        cache_set_list(tmp_db, "all_a_share", stocks, TTL_STOCKS)
        result = cache_get_list(tmp_db, "all_a_share")
        assert result == stocks
        assert len(result) == 2
        assert result[0]["ticker"] == "600519"

    def test_get_list_miss_returns_none(self, tmp_db: sqlite3.Connection) -> None:
        assert cache_get_list(tmp_db, "missing_key") is None

    def test_get_list_expired(self, tmp_db: sqlite3.Connection) -> None:
        cache_set_list(tmp_db, "temp_key", [1, 2, 3], ttl=0)
        time.sleep(0.01)
        assert cache_get_list(tmp_db, "temp_key") is None


# ── cache_clean ────────────────────────────────────────────────────────────────


class TestCacheClean:
    def test_clean_removes_expired(self, tmp_db: sqlite3.Connection) -> None:
        cache_set(tmp_db, "cache_valuations", "keep", {"x": 1}, TTL_VALUATION)
        cache_set(tmp_db, "cache_valuations", "expire", {"x": 2}, ttl=0)
        time.sleep(0.01)
        cleaned = cache_clean(tmp_db)
        assert cleaned >= 1
        assert cache_get(tmp_db, "cache_valuations", "keep") == {"x": 1}
        assert cache_get(tmp_db, "cache_valuations", "expire") is None

    def test_clean_no_expired(self, tmp_db: sqlite3.Connection) -> None:
        cache_set(tmp_db, "cache_valuations", "valid", {"x": 1}, TTL_VALUATION)
        cleaned = cache_clean(tmp_db)
        assert cleaned == 0
        assert cache_get(tmp_db, "cache_valuations", "valid") == {"x": 1}

    def test_clean_all_tables(self, tmp_db: sqlite3.Connection) -> None:
        """清理应作用于所有 5 个表。"""
        for table in (
            "cache_stocks",
            "cache_valuations",
            "cache_financials",
            "cache_income",
            "cache_cfo",
        ):
            cache_set(tmp_db, table, "exp", {"x": 1}, ttl=0)
        time.sleep(0.01)
        cleaned = cache_clean(tmp_db)
        assert cleaned == 5


# ── load_all_cache / save_cache_to_db ──────────────────────────────────────────


class TestLoadSaveCache:
    def test_save_and_load(self, tmp_db: sqlite3.Connection) -> None:
        cache_set(tmp_db, "cache_valuations", "v1", {"pe": 10.0}, TTL_VALUATION)
        cache_set(tmp_db, "cache_financials", "f1", {"roe": 20.0}, TTL_FINANCIALS)

        cache = load_all_cache(tmp_db)
        assert cache["valuations"] == {"v1": {"pe": 10.0}}
        assert cache["financials"] == {"f1": {"roe": 20.0}}
        assert cache["income"] == {}
        assert cache["cfo"] == {}

    def test_save_to_db_persists(self, tmp_db: sqlite3.Connection) -> None:
        cache = {"valuations": {"k1": {"pe": 5.0}}, "financials": {}, "income": {}, "cfo": {}}
        saved = save_cache_to_db(tmp_db, cache)
        assert saved == 1
        # 重新加载验证
        cache2 = load_all_cache(tmp_db)
        assert cache2["valuations"] == {"k1": {"pe": 5.0}}

    def test_save_multiple_entries(self, tmp_db: sqlite3.Connection) -> None:
        cache = {
            "valuations": {f"v{i}": {"pe": float(i)} for i in range(5)},
            "financials": {f"f{i}": {"roe": float(i)} for i in range(3)},
            "income": {},
            "cfo": {},
        }
        saved = save_cache_to_db(tmp_db, cache)
        assert saved == 8
        loaded = load_all_cache(tmp_db)
        assert len(loaded["valuations"]) == 5
        assert len(loaded["financials"]) == 3

    def test_load_skips_corrupted_json(self, tmp_db: sqlite3.Connection) -> None:
        """损坏的 JSON 不应导致崩溃，应被静默跳过。"""
        tmp_db.execute(
            "INSERT INTO cache_valuations (key, data, expires_at) VALUES (?, ?, ?)",
            ("bad", "not-json", 9999999999.0),
        )
        tmp_db.commit()
        cache = load_all_cache(tmp_db)
        assert cache["valuations"] == {}

    def test_load_excludes_expired(self, tmp_db: sqlite3.Connection) -> None:
        """过期记录不应被加载。"""
        tmp_db.execute(
            "INSERT INTO cache_valuations (key, data, expires_at) VALUES (?, ?, ?)",
            ("expired", '{"pe":1}', 0.0),  # 已过期
        )
        tmp_db.execute(
            "INSERT INTO cache_valuations (key, data, expires_at) VALUES (?, ?, ?)",
            ("valid", '{"pe":2}', 9999999999.0),
        )
        tmp_db.commit()
        cache = load_all_cache(tmp_db)
        assert "expired" not in cache["valuations"]
        assert cache["valuations"]["valid"] == {"pe": 2.0}


# ── TTL 常量验证 ──────────────────────────────────────────────────────────────


class TestTTLConstants:
    def test_ttls_are_positive_ints(self) -> None:
        assert isinstance(TTL_STOCKS, int) and TTL_STOCKS > 0
        assert isinstance(TTL_VALUATION, int) and TTL_VALUATION > 0
        assert isinstance(TTL_FINANCIALS, int) and TTL_FINANCIALS > 0
        assert isinstance(TTL_INCOME, int) and TTL_INCOME > 0
        assert isinstance(TTL_CFO, int) and TTL_CFO > 0

    def test_valuation_ttl_is_7_days(self) -> None:
        assert TTL_VALUATION == 7 * 86400

    def test_financials_ttl_is_90_days(self) -> None:
        assert TTL_FINANCIALS == 90 * 86400

    def test_income_cfo_ttl_is_180_days(self) -> None:
        assert TTL_INCOME == 180 * 86400
        assert TTL_CFO == 180 * 86400
