"""tests for scripts.fetch_reference_data."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from scripts.fetch_reference_data import _fetch_metadata_one


class TestGetAllTickers:
    """Ticker retrieval from DB."""

    def test_returns_list(self) -> None:
        with patch("scripts._utils.sqlite3.connect") as mock_connect:
            mock_conn = mock_connect.return_value
            mock_conn.execute.return_value.fetchall.return_value = [
                ("sh.600519",),
                ("sz.000001",),
            ]
            from scripts._utils import get_all_tickers

            tickers = get_all_tickers()
            assert isinstance(tickers, list)
            assert len(tickers) > 0

    def test_contains_known_tickers(self) -> None:
        """At minimum sz.000001 should always be present (whitelist stock)."""
        with patch("scripts._utils.sqlite3.connect") as mock_connect:
            mock_conn = mock_connect.return_value
            mock_conn.execute.return_value.fetchall.return_value = [
                ("sz.000001",),
                ("sh.600519",),
            ]
            from scripts._utils import get_all_tickers

            tickers = get_all_tickers()
            assert "sz.000001" in tickers
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
