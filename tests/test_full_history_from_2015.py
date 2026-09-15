"""tests for scripts.full_history_from_2015."""

from __future__ import annotations

import sqlite3
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd

from scripts._utils import get_all_tickers
from scripts.full_history_from_2015 import (
    END_DATE,
    START_DATE,
    _fetch_and_append,
)


def _make_df(n_rows: int, start: str = "2020-01-01") -> pd.DataFrame:
    """Create a mock K-line DataFrame with n_rows rows."""
    return pd.DataFrame(
        {
            "timestamps": pd.date_range(start, periods=n_rows, freq="D"),
            "open": [1.0] * n_rows,
            "high": [2.0] * n_rows,
            "low": [0.5] * n_rows,
            "close": [1.5] * n_rows,
            "volume": [100] * n_rows,
        }
    )


def _init_tmp_db(db_path: Path) -> None:
    """Create a minimal kline_cache table in a temp SQLite DB."""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE kline_cache ("
        "ticker TEXT PRIMARY KEY, start TEXT, end TEXT, freq TEXT, "
        "adjustflag TEXT, data BLOB, ttl REAL)"
    )
    conn.commit()
    conn.close()


def _seed_db(db_path: Path, ticker: str, start: str, end: str, n_rows: int = 200) -> None:
    """Insert an existing record into the temp DB."""
    df = _make_df(n_rows, start)
    buf = BytesIO()
    df.to_pickle(buf)
    buf.seek(0)
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT OR REPLACE INTO kline_cache (ticker, start, end, freq, adjustflag, data, ttl) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (ticker, start, end, "d", "1", buf.read(), 0.0),
    )
    conn.commit()
    conn.close()


class TestConstants:
    """Module-level constants."""

    def test_start_date(self) -> None:
        assert START_DATE == "2015-01-01"

    def test_end_date(self) -> None:
        assert END_DATE == "2026-09-15"


class TestGetAllTickers:
    """Ticker retrieval."""

    def test_returns_list(self) -> None:
        tickers = get_all_tickers()
        assert isinstance(tickers, list)
        assert len(tickers) > 0

    def test_sorted(self) -> None:
        tickers = get_all_tickers()
        assert tickers == sorted(tickers)


class TestFetchAndAppend:
    """Historical data fetch and merge logic."""

    def test_already_full(self, tmp_path: Path) -> None:
        """Stock with existing full history covering new data should return already_full."""
        tmp_db = tmp_path / "test.db"
        _init_tmp_db(tmp_db)
        # Pre-seed: existing data covers 2020-01-01 onward
        _seed_db(tmp_db, "sh.600519", "2020-01-01", "2026-09-14", n_rows=2418)

        mock_factory = MagicMock()
        mock_provider = MagicMock()
        # Mock returns same date range → all rows are within existing range
        df = _make_df(2418, "2020-01-01")
        mock_result = MagicMock()
        mock_result.is_empty = False
        mock_result.to_dataframe.return_value = df
        mock_provider.fetch_kline.return_value = mock_result
        mock_factory.get_provider.return_value = mock_provider

        ticker, n_new, status = _fetch_and_append(
            mock_factory,
            "sh.600519",
            ["baostock"],
            "2015-01-01",
            "2026-09-15",
            tmp_db,
        )
        assert status == "already_full"
        assert n_new == 0

    def test_fail_no_provider(self, tmp_path: Path) -> None:
        """When no provider returns data, should return FAIL."""
        tmp_db = tmp_path / "test.db"
        _init_tmp_db(tmp_db)
        mock_factory = MagicMock()
        mock_factory.get_provider.return_value = None

        ticker, n_new, status = _fetch_and_append(
            mock_factory,
            "sh.999999",
            ["baostock"],
            "2015-01-01",
            "2026-09-15",
            tmp_db,
        )
        assert status == "FAIL"
        assert n_new == 0

    def test_exception_handling(self, tmp_path: Path) -> None:
        """Exceptions in _fetch_and_append are caught and returned as ERROR."""
        tmp_db = tmp_path / "test.db"
        _init_tmp_db(tmp_db)
        mock_factory = MagicMock()
        mock_factory.get_provider.side_effect = RuntimeError("connection lost")

        ticker, n_new, status = _fetch_and_append(
            mock_factory,
            "sh.600519",
            ["baostock"],
            "2015-01-01",
            "2026-09-15",
            tmp_db,
        )
        assert n_new == 0
        assert status == "FAIL"  # outer exception caught → FAIL (provider chain exhausted)

    def test_new_data_appended(self, tmp_path: Path) -> None:
        """New historical data before existing range should be appended."""
        tmp_db = tmp_path / "test.db"
        _init_tmp_db(tmp_db)
        # Existing data starts from 2020-01-01
        _seed_db(tmp_db, "sh.600519", "2020-01-01", "2026-09-14", n_rows=200)

        mock_factory = MagicMock()
        mock_provider = MagicMock()
        # Mock returns data from 2015-01-01 to 2019-12-31 (before existing)
        df = _make_df(1826, "2015-01-01")
        mock_result = MagicMock()
        mock_result.is_empty = False
        mock_result.to_dataframe.return_value = df
        mock_provider.fetch_kline.return_value = mock_result
        mock_factory.get_provider.return_value = mock_provider

        ticker, n_new, status = _fetch_and_append(
            mock_factory,
            "sh.600519",
            ["baostock"],
            "2015-01-01",
            "2026-09-15",
            tmp_db,
        )
        # Should append the 1826 old rows
        assert status == f"{n_new}rows"
        assert n_new == 1826

    def test_no_existing_record(self, tmp_path: Path) -> None:
        """Stock with no existing record should store new data."""
        tmp_db = tmp_path / "test.db"
        _init_tmp_db(tmp_db)

        mock_factory = MagicMock()
        mock_provider = MagicMock()
        df = _make_df(100, "2020-01-01")
        mock_result = MagicMock()
        mock_result.is_empty = False
        mock_result.to_dataframe.return_value = df
        mock_provider.fetch_kline.return_value = mock_result
        mock_factory.get_provider.return_value = mock_provider

        ticker, n_new, status = _fetch_and_append(
            mock_factory,
            "sh.999000",
            ["baostock"],
            "2015-01-01",
            "2026-09-15",
            tmp_db,
        )
        assert status == "100rows"
        assert n_new == 100
