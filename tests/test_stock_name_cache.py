"""tests for scripts.stock_name_cache (股票名称本地 SQLite 缓存)."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

import pytest

from scripts.stock_name_cache import (
    STOCK_NAME_TABLE,
    batch_get_cached_stock_names,
    batch_write_stock_name_cache,
    ensure_stock_name_table,
    get_cached_stock_name,
    get_missing_name_tickers,
    refresh_missing_stock_names,
    seed_stock_name_cache_from_map,
)


@pytest.fixture
def conn():
    """临时 SQLite 连接（每测试函数一个独立文件，隔离状态）。"""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    c = sqlite3.connect(path)
    yield c
    c.close()
    Path(path).unlink(missing_ok=True)


class TestEnsureAndRead:
    """建表与读取。"""

    def test_ensure_table_idempotent(self, conn: sqlite3.Connection) -> None:
        """建表可重复调用，不报错。"""
        ensure_stock_name_table(conn)
        ensure_stock_name_table(conn)
        # 表应存在
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (STOCK_NAME_TABLE,),
        )
        assert cur.fetchone() is not None

    def test_get_cached_missing_returns_none(self, conn: sqlite3.Connection) -> None:
        """空表查任意 ticker 返回 None。"""
        ensure_stock_name_table(conn)
        assert get_cached_stock_name(conn, "sh.600519") is None

    def test_write_then_read(self, conn: sqlite3.Connection) -> None:
        """写入后能读回。"""
        ensure_stock_name_table(conn)
        batch_write_stock_name_cache(conn, [("sh.600519", "贵州茅台")])
        assert get_cached_stock_name(conn, "sh.600519") == "贵州茅台"

    def test_upsert_updates_name(self, conn: sqlite3.Connection) -> None:
        """同一 ticker 重复写入会更新名称（幂等）。"""
        ensure_stock_name_table(conn)
        batch_write_stock_name_cache(conn, [("sh.600519", "贵州茅台")])
        batch_write_stock_name_cache(conn, [("sh.600519", "Kweichow Moutai")])
        assert get_cached_stock_name(conn, "sh.600519") == "Kweichow Moutai"


class TestBatchRead:
    """批量读取。"""

    def test_batch_get_cached_stock_names(self, conn: sqlite3.Connection) -> None:
        """只返回命中项，缺失的不在结果里。"""
        ensure_stock_name_table(conn)
        batch_write_stock_name_cache(
            conn,
            [
                ("sh.600519", "贵州茅台"),
                ("sz.000858", "五粮液"),
            ],
        )
        got = batch_get_cached_stock_names(
            conn, ["sh.600519", "sz.000858", "sh.000001"]
        )
        assert got == {"sh.600519": "贵州茅台", "sz.000858": "五粮液"}

    def test_batch_get_empty_input(self, conn: sqlite3.Connection) -> None:
        """空输入返回空 dict。"""
        ensure_stock_name_table(conn)
        assert batch_get_cached_stock_names(conn, []) == {}


class TestMissingTickers:
    """缺失名称 ticker 判定。"""

    def test_get_missing_name_tickers(self, conn: sqlite3.Connection) -> None:
        """只返回尚未缓存的 ticker，保持顺序并去重。"""
        ensure_stock_name_table(conn)
        batch_write_stock_name_cache(conn, [("sh.600519", "贵州茅台")])
        missing = get_missing_name_tickers(
            conn, ["sh.600519", "sz.000858", "sh.600519", "sh.000001"]
        )
        assert missing == ["sz.000858", "sh.000001"]

    def test_all_cached_returns_empty(self, conn: sqlite3.Connection) -> None:
        """全部已缓存时返回空列表。"""
        ensure_stock_name_table(conn)
        batch_write_stock_name_cache(
            conn, [("sh.600519", "贵州茅台"), ("sz.000858", "五粮液")]
        )
        assert get_missing_name_tickers(conn, ["sh.600519", "sz.000858"]) == []


class TestRefreshMissing:
    """仅补缺失名称。"""

    def test_refresh_only_missing(self, conn: sqlite3.Connection) -> None:
        """已命中不联网，仅补缺失。"""
        ensure_stock_name_table(conn)
        batch_write_stock_name_cache(conn, [("sh.600519", "贵州茅台")])

        def query_name(ticker: str) -> str | None:
            # 模拟网络查询：只对 sz.000858 有结果
            if ticker == "sz.000858":
                return "五粮液"
            return None

        newly = refresh_missing_stock_names(
            conn, ["sh.600519", "sz.000858", "sh.000001"], query_name
        )
        # sh.600519 已命中不查；sz.000858 补成功；sh.000001 查不到不写入
        assert newly == {"sz.000858": "五粮液"}
        assert get_cached_stock_name(conn, "sz.000858") == "五粮液"
        # sh.000001 查不到不写入
        assert get_cached_stock_name(conn, "sh.000001") is None

    def test_query_failure_does_not_write(self, conn: sqlite3.Connection) -> None:
        """查询抛异常不写入，不中断整批。"""
        ensure_stock_name_table(conn)

        def flaky_query(ticker: str) -> str | None:
            if ticker == "sh.600519":
                raise RuntimeError("boom")
            return "Name"

        newly = refresh_missing_stock_names(
            conn, ["sh.600519", "sz.000858"], flaky_query
        )
        assert newly == {"sz.000858": "Name"}
        assert get_cached_stock_name(conn, "sh.600519") is None


class TestSeed:
    """批量种入名称。"""

    def test_seed_stock_name_cache_from_map(self, conn: sqlite3.Connection) -> None:
        """种入非空名称，空名称被跳过。"""
        ensure_stock_name_table(conn)
        name_map = {
            "sh.600519": "贵州茅台",
            "sz.000858": "",  # 空名称应被跳过
            "sh.000001": "平安银行",
        }
        written = seed_stock_name_cache_from_map(conn, name_map)
        assert written == 2
        assert get_cached_stock_name(conn, "sh.600519") == "贵州茅台"
        assert get_cached_stock_name(conn, "sh.000001") == "平安银行"
        assert get_cached_stock_name(conn, "sz.000858") is None
