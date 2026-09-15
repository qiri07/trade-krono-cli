"""tests for scripts.full_history_from_2015."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd

from scripts._utils import get_all_tickers
from scripts.full_history_from_2015 import (
    END_DATE,
    START_DATE,
    _fetch_and_append,
)


def _make_df(n_rows: int) -> pd.DataFrame:
    """Create a mock K-line DataFrame with n_rows rows."""
    return pd.DataFrame(
        {
            "timestamps": pd.date_range("2020-01-01", periods=n_rows, freq="D"),
            "open": [1.0] * n_rows,
            "high": [2.0] * n_rows,
            "low": [0.5] * n_rows,
            "close": [1.5] * n_rows,
            "volume": [100] * n_rows,
        }
    )


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

    def test_already_full(self) -> None:
        """Stock with existing full history should return already_full."""
        mock_factory = MagicMock()
        mock_provider = MagicMock()
        # Return data entirely after 2020 - should be considered already_full
        df = _make_df(2418)
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
            Path("outputs/cache/pipeline_cache.db"),
        )
        assert status == "already_full"
        assert n_new == 0

    def test_fail_no_provider(self) -> None:
        """When no provider returns data, should return FAIL."""
        mock_factory = MagicMock()
        mock_factory.get_provider.return_value = None

        ticker, n_new, status = _fetch_and_append(
            mock_factory,
            "sh.999999",
            ["baostock"],
            "2015-01-01",
            "2026-09-15",
            Path("outputs/cache/pipeline_cache.db"),
        )
        assert status == "FAIL"
        assert n_new == 0

    def test_exception_handling(self) -> None:
        """Exceptions in _fetch_and_append are caught and returned as FAIL."""
        mock_factory = MagicMock()
        mock_factory.get_provider.side_effect = RuntimeError("connection lost")

        ticker, n_new, status = _fetch_and_append(
            mock_factory,
            "sh.600519",
            ["baostock"],
            "2015-01-01",
            "2026-09-15",
            Path("outputs/cache/pipeline_cache.db"),
        )
        assert n_new == 0
        assert status in ("FAIL", "ERROR:")
