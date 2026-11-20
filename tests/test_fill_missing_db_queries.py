"""tests for scripts.fill_missing.db_queries — 历史充足性判定（按行内 K 线根数）。

回归背景：``kline_cache`` 每个 ticker 通常只有 1 行（整段日线 pickle 在 ``data`` 列），
历史上用 ``COUNT(*)`` 统计表行数做判定，导致每只票都被判为「历史不足」，
补拉计划因此包含近万只无需补拉的 ticker。
"""

from __future__ import annotations

import pickle
import sqlite3

import pandas as pd
import pytest

from scripts.fill_missing import db_queries


def _frame(bars: int) -> pd.DataFrame:
    """构造含指定根数的日线 DataFrame。"""
    return pd.DataFrame(
        {
            "timestamps": pd.date_range("2020-01-02", periods=bars, freq="D"),
            "open": [1.0] * bars,
            "close": [1.0] * bars,
        }
    )


def _insert(
    conn: sqlite3.Connection,
    ticker: str,
    start: str,
    end: str,
    blob: bytes,
) -> None:
    conn.execute(
        "INSERT INTO kline_cache (ticker, start, end, freq, adjustflag, ttl, data, created)"
        " VALUES (?, ?, ?, 'd', '1', 0.0, ?, 0.0)",
        (ticker, start, end, blob),
    )


@pytest.fixture()
def db_path(tmp_path, monkeypatch) -> str:
    """建一个只含 kline_cache 的临时库，并把 db_queries.CACHE_DB 指过去。"""
    path = tmp_path / "pipeline_cache.db"
    conn = sqlite3.connect(str(path))
    conn.execute(
        """CREATE TABLE kline_cache (
            ticker TEXT NOT NULL,
            start TEXT NOT NULL,
            end TEXT NOT NULL,
            freq TEXT NOT NULL,
            adjustflag TEXT NOT NULL DEFAULT '1',
            ttl REAL NOT NULL,
            data BLOB NOT NULL,
            created REAL NOT NULL,
            PRIMARY KEY (ticker, start, end, freq, adjustflag)
        )"""
    )
    # 完整窗口：start 不晚于 FULL_HISTORY_START，走廉价预筛分支
    _insert(conn, "sh.600519", "2020-01-02", "2026-11-19", pickle.dumps(_frame(1667)))
    # 新股，起始日晚于窗口起点，但真实根数充足
    _insert(conn, "sh.688999", "2023-01-03", "2026-11-19", pickle.dumps(_frame(680)))
    # 次新 ETF：上市仅 4 个多月，绝对根数不足阈值但首末区间内数据连续（密度达标）
    _insert(conn, "sh.510140", "2026-07-03", "2026-11-19", pickle.dumps(_frame(94)))
    # 抓取失败留下的残缺数据（中间大量空洞，密度不达标）
    _insert(conn, "sz.300001", "2026-10-15", "2026-11-19", pickle.dumps(_frame(9)))
    # 北交所不在补拉范围
    _insert(conn, "bj.920001", "2026-10-15", "2026-11-19", pickle.dumps(_frame(5)))
    conn.commit()
    conn.close()
    monkeypatch.setattr(db_queries, "CACHE_DB", path)
    return str(path)


class TestGetInsufficientHistoryTickers:
    """get_insufficient_history_tickers 按真实 K 线根数判定，而非表行数。"""

    def test_only_sparse_ticker_is_reported(self, db_path) -> None:
        """只有真实根数 < 阈值的非北交所 ticker 被报为历史不足。"""
        assert db_queries.get_insufficient_history_tickers(db_path) == ["sz.300001"]

    def test_full_window_ticker_not_reported(self, db_path) -> None:
        """start 覆盖完整窗口的 ticker 即使未反序列化也不报不足。"""
        result = db_queries.get_insufficient_history_tickers(db_path)
        assert "sh.600519" not in result

    def test_new_listing_with_dense_history_not_reported(self, db_path) -> None:
        """新股起始日晚但数据完整（680 根）不应被判为不足。"""
        result = db_queries.get_insufficient_history_tickers(db_path)
        assert "sh.688999" not in result

    def test_new_etf_dense_but_short_not_reported(self, db_path) -> None:
        """次新 ETF 绝对根数（94）低于阈值，但区间内数据连续 → 不判为不足。"""
        result = db_queries.get_insufficient_history_tickers(db_path)
        assert "sh.510140" not in result

    def test_bse_ticker_excluded(self, db_path) -> None:
        """北交所（bj.）不参与补拉。"""
        result = db_queries.get_insufficient_history_tickers(db_path)
        assert not any(t.startswith("bj.") for t in result)

    def test_corrupt_pickle_counted_as_zero(self, tmp_path, monkeypatch) -> None:
        """data 列损坏时按 0 根计，不抛异常，且被判为历史不足。"""
        path = tmp_path / "broken.db"
        conn = sqlite3.connect(str(path))
        conn.execute("CREATE TABLE kline_cache (ticker TEXT, start TEXT, end TEXT, data BLOB)")
        conn.execute(
            "INSERT INTO kline_cache VALUES ('sz.000002', '2026-10-15', '2026-11-19', X'00')"
        )
        conn.commit()
        conn.close()
        monkeypatch.setattr(db_queries, "CACHE_DB", path)
        assert db_queries.get_insufficient_history_tickers(path) == ["sz.000002"]

    def test_get_failed_tickers_delegates(self, db_path) -> None:
        """get_failed_tickers 与主判定口径一致。"""
        assert db_queries.get_failed_tickers() == ["sz.300001"]


class TestHasSufficientHistory:
    """_has_sufficient_history 单只票判定（含两段式：预筛 + 精确计数）。"""

    def test_full_window_returns_true(self, db_path) -> None:
        assert db_queries._has_sufficient_history("sh.600519") is True

    def test_dense_new_listing_returns_true(self, db_path) -> None:
        assert db_queries._has_sufficient_history("sh.688999") is True

    def test_dense_but_short_etf_returns_true(self, db_path) -> None:
        """次新 ETF：绝对根数不足但密度达标 → 视为历史充足。"""
        assert db_queries._has_sufficient_history("sh.510140") is True

    def test_sparse_history_returns_false(self, db_path) -> None:
        assert db_queries._has_sufficient_history("sz.300001") is False

    def test_unknown_ticker_returns_false(self, db_path) -> None:
        assert db_queries._has_sufficient_history("sh.000000") is False

    def test_multiple_rows_are_summed(self, tmp_path, monkeypatch) -> None:
        """同一 ticker 存在多段记录时，根数按段累加。"""
        path = tmp_path / "multi.db"
        conn = sqlite3.connect(str(path))
        conn.execute("CREATE TABLE kline_cache (ticker TEXT, start TEXT, end TEXT, data BLOB)")
        for i in range(3):
            conn.execute(
                "INSERT INTO kline_cache VALUES ('sz.000003', '2026-10-15', '2026-10-20', ?)",
                (pickle.dumps(_frame(40)),),
            )
        conn.commit()
        conn.close()
        monkeypatch.setattr(db_queries, "CACHE_DB", path)
        # 3 段 × 40 根 = 120 ≥ 100，即使每段单独都不到阈值
        assert db_queries._has_sufficient_history("sz.000003") is True


class TestGetTodayOnlyTickers:
    """get_today_only_tickers 以预期交易日为准（而非原始今日）。"""

    def test_matches_expected_trading_day(self, tmp_path, monkeypatch) -> None:
        path = tmp_path / "today.db"
        conn = sqlite3.connect(str(path))
        conn.execute("CREATE TABLE kline_cache (ticker TEXT, start TEXT, end TEXT, data BLOB)")
        conn.execute(
            "INSERT INTO kline_cache VALUES ('sh.600519', '2026-11-19', '2026-11-19', X'00')"
        )
        conn.execute(
            "INSERT INTO kline_cache VALUES ('sz.000001', '2026-11-20', '2026-11-20', X'00')"
        )
        conn.commit()
        conn.close()
        monkeypatch.setattr(db_queries, "CACHE_DB", path)
        monkeypatch.setattr(db_queries, "get_expected_date", lambda: "2026-11-19")
        assert db_queries.get_today_only_tickers() == ["sh.600519"]
