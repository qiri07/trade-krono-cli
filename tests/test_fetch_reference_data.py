"""tests for scripts.fetch_reference_data."""

from __future__ import annotations

from unittest.mock import MagicMock

from scripts._utils import get_all_tickers
from scripts.fetch_reference_data import _fetch_metadata_one


class TestGetAllTickers:
    """Ticker retrieval from DB."""

    def test_returns_list(self) -> None:
        tickers = get_all_tickers()
        assert isinstance(tickers, list)
        assert len(tickers) > 0

    def test_contains_known_tickers(self) -> None:
        """At minimum sz.000001 should always be present (whitelist stock)."""
        tickers = get_all_tickers()
        assert "sz.000001" in tickers
        # sh.600519 may not be present after cache clear; just check non-empty
        assert len(tickers) > 0


class TestFetchMetadataOne:
    """Single stock metadata fetch."""

    def test_success(self) -> None:
        """Successful metadata fetch returns correct structure."""
        mock_factory = MagicMock()
        mock_meta = MagicMock()
        mock_meta.ticker = "sh.600519"
        mock_meta.industry = "银行"
        mock_meta.pe_ttm = 12.5
        mock_meta.pb = 1.2
        mock_meta.ipo_date = "2001-08-27"
        mock_meta.is_st = False
        mock_meta.source = "tonghuashun"
        mock_factory.fetch_metadata.return_value = mock_meta

        ticker, meta, status = _fetch_metadata_one(mock_factory, "sh.600519")
        assert status == "ok"
        assert meta is not None
        assert meta["ticker"] == "sh.600519"
        assert meta["industry"] == "银行"
        assert meta["ipo_date"] == "2001-08-27"

    def test_none_result(self) -> None:
        """When fetch_metadata returns None, status is 'none'."""
        mock_factory = MagicMock()
        mock_factory.fetch_metadata.return_value = None

        ticker, meta, status = _fetch_metadata_one(mock_factory, "sh.999999")
        assert status == "none"
        assert meta is None

    def test_exception(self) -> None:
        """Exceptions are caught and returned as error status."""
        mock_factory = MagicMock()
        mock_factory.fetch_metadata.side_effect = RuntimeError("API timeout")

        ticker, meta, status = _fetch_metadata_one(mock_factory, "sh.600519")
        assert status.startswith("error:")
        assert meta is None
