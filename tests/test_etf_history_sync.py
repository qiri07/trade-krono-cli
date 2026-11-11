"""`scripts.etf_history_sync` 单元测试：代码归一化与供应商降级。"""

from __future__ import annotations

import io
import sqlite3
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pandas as pd

import scripts.etf_history_sync as ehs

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _frame(dates: list[str]) -> pd.DataFrame:
    """构造 N 日 K 线 DataFrame。"""
    return pd.DataFrame(
        {
            "timestamps": pd.to_datetime(dates),
            "open": [1.0] * len(dates),
            "high": [1.1] * len(dates),
            "low": [0.9] * len(dates),
            "close": [1.05] * len(dates),
            "volume": [1000.0] * len(dates),
            "amount": [1050.0] * len(dates),
        }
    )


class _FakeResult:
    """模拟 provider.fetch_kline 返回值。"""

    def __init__(self, df: pd.DataFrame) -> None:
        self._df = df
        self.is_empty = df.empty

    def to_dataframe(self) -> pd.DataFrame:
        return self._df


class TestEtfCodeToTicker:
    """ETF 代码 → 带前缀 ticker。"""

    def test_sh_segments(self) -> None:
        for code in ("510300", "520700", "530880", "551000", "562730", "588000", "501030"):
            assert ehs.etf_code_to_ticker(code) == f"sh.{code}"

    def test_sz_segments(self) -> None:
        for code in ("159915", "156100", "158016"):
            assert ehs.etf_code_to_ticker(code) == f"sz.{code}"

    def test_unknown_returns_none(self) -> None:
        assert ehs.etf_code_to_ticker("600519") is None
        assert ehs.etf_code_to_ticker("000001") is None


class TestFetchAndAppend:
    """增量合并 + 供应商滞后降级。"""

    @staticmethod
    def _existing_db(tmp_path: Path, ticker: str, dates: list[str]) -> Path:
        db = tmp_path / "cache.db"
        conn = sqlite3.connect(str(db))
        conn.execute(
            "CREATE TABLE kline_cache (ticker TEXT, start TEXT, end TEXT, freq TEXT, data BLOB)"
        )
        buf = io.BytesIO()
        _frame(dates).to_pickle(buf)
        conn.execute(
            "INSERT INTO kline_cache VALUES (?, '2020-01-01', ?, 'd', ?)",
            (ticker, dates[-1], buf.getvalue()),
        )
        conn.commit()
        conn.close()
        return db

    def test_falls_back_when_first_provider_is_stale(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ticker = "sh.510300"
        db = self._existing_db(tmp_path, ticker, ["2026-11-07", "2026-11-09"])

        stale_provider = MagicMock()
        stale_provider.fetch_kline.return_value = _FakeResult(_frame(["2026-11-07", "2026-11-09"]))
        fresh_provider = MagicMock()
        fresh_provider.fetch_kline.return_value = _FakeResult(
            _frame(["2026-11-07", "2026-11-09", "2026-11-10"])
        )

        factory = MagicMock()
        factory.get_provider.side_effect = lambda name: {
            "stale": stale_provider,
            "fresh": fresh_provider,
        }[name]
        cache = MagicMock()

        monkeypatch.setattr(ehs, "get_provider_chain", lambda _t: ["stale", "fresh"])
        monkeypatch.setattr(
            ehs, "get_sqlite_conn", lambda _db: sqlite3.connect(str(db))
        )
        monkeypatch.setattr(ehs, "get_cache", lambda: cache)
        monkeypatch.setattr(ehs, "expected_trading_day", lambda: "2026-11-10")

        got_ticker, _rows, detail = ehs.fetch_and_append(
            factory, ticker, "2026-10-11", "2026-11-11"
        )

        assert got_ticker == ticker
        assert detail == "fresh"
        cache.set_kline.assert_called_once()
        merged = cache.set_kline.call_args.args[4]
        assert len(merged) == 3
        assert merged["timestamps"].iloc[-1] == pd.Timestamp("2026-11-10")
        assert merged["timestamps"].is_unique

    def test_reports_up_to_date_when_all_providers_stale(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ticker = "sh.510300"
        db = self._existing_db(tmp_path, ticker, ["2026-11-09"])

        stale = MagicMock()
        stale.fetch_kline.return_value = _FakeResult(_frame(["2026-11-09"]))
        factory = MagicMock()
        factory.get_provider.return_value = stale
        cache = MagicMock()

        monkeypatch.setattr(ehs, "get_provider_chain", lambda _t: ["p1", "p2"])
        monkeypatch.setattr(
            ehs, "get_sqlite_conn", lambda _db: sqlite3.connect(str(db))
        )
        monkeypatch.setattr(ehs, "get_cache", lambda: cache)
        monkeypatch.setattr(ehs, "expected_trading_day", lambda: "2026-11-10")

        _t, _n, detail = ehs.fetch_and_append(factory, ticker, "2026-10-11", "2026-11-11")

        assert detail == "up_to_date"
        cache.set_kline.assert_not_called()

    def test_returns_fail_when_no_provider_has_data(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        db = self._existing_db(tmp_path, "sh.510300", ["2026-11-09"])
        empty = MagicMock()
        empty.fetch_kline.return_value = None
        factory = MagicMock()
        factory.get_provider.return_value = empty

        monkeypatch.setattr(ehs, "get_provider_chain", lambda _t: ["p1"])
        monkeypatch.setattr(
            ehs, "get_sqlite_conn", lambda _db: sqlite3.connect(str(db))
        )
        monkeypatch.setattr(ehs, "get_cache", lambda: MagicMock())
        monkeypatch.setattr(ehs, "expected_trading_day", lambda: "2026-11-10")

        _t, _n, detail = ehs.fetch_and_append(factory, "sh.510300", "2026-10-11", "2026-11-11")
        assert detail == "FAIL"
