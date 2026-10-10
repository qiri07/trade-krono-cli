"""tests for scripts.retry_stale_stocks — 滞后股票重试补齐。"""

from __future__ import annotations

from io import BytesIO
from unittest.mock import MagicMock, patch

import pandas as pd


def _make_kline_pickle(n: int = 10) -> bytes:
    dates = pd.date_range(start="2026-09-01", periods=n, freq="B")
    df = pd.DataFrame(
        {
            "timestamps": dates.strftime("%Y-%m-%d").tolist(),
            "open": [100.0 + i for i in range(n)],
            "high": [101.0 + i for i in range(n)],
            "low": [99.0 + i for i in range(n)],
            "close": [100.5 + i for i in range(n)],
            "volume": [1_000_000] * n,
        }
    )
    buf = BytesIO()
    df.to_pickle(buf)
    return buf.getvalue()


class TestGetStaleTickers:
    """get_stale_tickers 测试。"""

    def test_returns_tickers_with_old_end(self) -> None:
        from scripts.retry_stale_stocks import get_stale_tickers

        mock_rows = [("sh.600519",), ("sz.000001",)]
        with patch("scripts.retry_stale_stocks.sqlite3.connect") as mock_connect:
            mock_conn = mock_connect.return_value
            mock_conn.execute.return_value.fetchall.return_value = mock_rows
            result = get_stale_tickers()
        assert len(result) == 2
        assert "sh.600519" in result

    def test_empty_when_all_current(self) -> None:
        from scripts.retry_stale_stocks import get_stale_tickers

        with patch("scripts.retry_stale_stocks.sqlite3.connect") as mock_connect:
            mock_conn = mock_connect.return_value
            mock_conn.execute.return_value.fetchall.return_value = []
            result = get_stale_tickers()
        assert result == []


class TestFetchAndMerge:
    """fetch_and_merge 测试。"""

    def _make_new_df(self) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "timestamps": ["2026-10-08"],
                "open": [110.0],
                "high": [111.0],
                "low": [109.0],
                "close": [110.5],
                "volume": [1_500_000],
            }
        )

    def test_first_provider_succeeds(self) -> None:
        from scripts.retry_stale_stocks import fetch_and_merge

        old_data = _make_kline_pickle(10)
        new_df = self._make_new_df()

        mock_provider = MagicMock()
        mock_result = MagicMock()
        mock_result.is_empty = False
        mock_result.to_dataframe.return_value = new_df
        mock_provider.fetch_kline.return_value = mock_result

        mock_factory = MagicMock()
        mock_factory.get_provider.side_effect = [mock_provider, None, None]

        with patch("scripts.retry_stale_stocks.sqlite3.connect") as mock_connect:
            mock_conn = mock_connect.return_value
            mock_conn.execute.return_value.fetchone.side_effect = [(old_data,), None]
            ticker, ok, msg = fetch_and_merge(mock_factory, "sh.600519")
        assert ok is True
        assert msg == "tonghuashun"

    def test_fallback_to_second_provider(self) -> None:
        from scripts.retry_stale_stocks import fetch_and_merge

        old_data = _make_kline_pickle(10)
        new_df = self._make_new_df()

        # First provider returns empty
        empty_provider = MagicMock()
        empty_result = MagicMock()
        empty_result.is_empty = True
        empty_provider.fetch_kline.return_value = empty_result

        # Second provider succeeds
        success_provider = MagicMock()
        success_result = MagicMock()
        success_result.is_empty = False
        success_result.to_dataframe.return_value = new_df
        success_provider.fetch_kline.return_value = success_result

        mock_factory = MagicMock()
        mock_factory.get_provider.side_effect = [empty_provider, success_provider, None]

        with patch("scripts.retry_stale_stocks.sqlite3.connect") as mock_connect:
            mock_conn = mock_connect.return_value
            mock_conn.execute.return_value.fetchone.side_effect = [(old_data,), None]
            ticker, ok, msg = fetch_and_merge(mock_factory, "sh.600519")
        assert ok is True
        assert msg == "baostock"

    def test_all_providers_fail(self) -> None:
        from scripts.retry_stale_stocks import fetch_and_merge

        mock_provider = MagicMock()
        empty_result = MagicMock()
        empty_result.is_empty = True
        mock_provider.fetch_kline.return_value = empty_result

        mock_factory = MagicMock()
        mock_factory.get_provider.return_value = mock_provider

        ticker, ok, msg = fetch_and_merge(mock_factory, "sh.999999")
        assert ok is False
        assert msg == "all_failed"

    def test_exception_caught_continues(self) -> None:
        from scripts.retry_stale_stocks import fetch_and_merge

        old_data = _make_kline_pickle(10)
        new_df = self._make_new_df()

        # First provider raises
        failing_provider = MagicMock()
        failing_provider.fetch_kline.side_effect = RuntimeError("timeout")

        # Second provider succeeds
        success_provider = MagicMock()
        success_result = MagicMock()
        success_result.is_empty = False
        success_result.to_dataframe.return_value = new_df
        success_provider.fetch_kline.return_value = success_result

        mock_factory = MagicMock()
        mock_factory.get_provider.side_effect = [failing_provider, success_provider, None]

        with patch("scripts.retry_stale_stocks.sqlite3.connect") as mock_connect:
            mock_conn = mock_connect.return_value
            # First fetchone for tonghuashun needs data; then None for update
            mock_conn.execute.return_value.fetchone.side_effect = [(old_data,), None]
            ticker, ok, msg = fetch_and_merge(mock_factory, "sh.600519")
        assert ok is True

    def test_no_provider_available(self) -> None:
        from scripts.retry_stale_stocks import fetch_and_merge

        mock_factory = MagicMock()
        mock_factory.get_provider.return_value = None

        ticker, ok, msg = fetch_and_merge(mock_factory, "sh.600519")
        assert ok is False
        assert msg == "all_failed"


class TestMain:
    """main 主流程测试。"""

    def test_no_stale_tickers(self) -> None:
        from scripts.retry_stale_stocks import main

        with (
            patch("scripts.retry_stale_stocks.get_stale_tickers", return_value=[]),
            patch("scripts.retry_stale_stocks.logger") as mock_logger,
        ):
            main()
            mock_logger.info.assert_called()
            calls = [c[0][0] for c in mock_logger.info.call_args_list]
            assert any("数据已是最新" in c for c in calls)

    def test_with_stale_tickers(self) -> None:
        from scripts.retry_stale_stocks import main

        stale = ["sh.600519", "sz.000001"]
        mock_factory = MagicMock()
        mock_provider = MagicMock()
        mock_result = MagicMock()
        mock_result.is_empty = False
        mock_result.to_dataframe.return_value = pd.DataFrame(
            {"timestamps": ["2026-10-08"], "open": [110.0], "high": [111.0], "low": [109.0], "close": [110.5], "volume": [1_000_000]}
        )
        mock_provider.fetch_kline.return_value = mock_result
        mock_factory.get_provider.return_value = mock_provider

        with (
            patch("scripts.retry_stale_stocks.get_stale_tickers", return_value=stale),
            patch("scripts.retry_stale_stocks.get_data_factory", return_value=mock_factory),
            patch("scripts.retry_stale_stocks.logger") as mock_logger,
            patch("scripts.retry_stale_stocks.sqlite3.connect"),
        ):
            main()
            mock_logger.info.assert_called()
