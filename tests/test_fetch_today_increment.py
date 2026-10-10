"""tests for scripts.fetch_today_increment — 今日增量 K 线拉取。"""

from __future__ import annotations

from io import BytesIO
from unittest.mock import MagicMock, patch

import pandas as pd


def _make_kline_pickle(n: int = 10) -> bytes:
    """生成测试用 K 线 pickle 数据。"""
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


class TestGetMaxEndDate:
    """get_max_end_date 测试。"""

    def test_returns_latest_date(self) -> None:
        from scripts.fetch_today_increment import get_max_end_date

        with patch("scripts.fetch_today_increment.sqlite3.connect") as mock_connect:
            mock_conn = mock_connect.return_value
            mock_conn.execute.return_value.fetchone.return_value = ("2026-10-09",)
            result = get_max_end_date()
        assert result == "2026-10-09"

    def test_no_data_returns_none(self) -> None:
        from scripts.fetch_today_increment import get_max_end_date

        with patch("scripts.fetch_today_increment.sqlite3.connect") as mock_connect:
            mock_conn = mock_connect.return_value
            mock_conn.execute.return_value.fetchone.return_value = (None,)
            result = get_max_end_date()
        assert result is None


class TestGetStaleTickers:
    """get_stale_tickers 测试。"""

    def test_returns_stale_tickers(self) -> None:
        from scripts.fetch_today_increment import get_stale_tickers

        mock_rows = [("sh.600519",), ("sz.000001",)]
        with patch("scripts.fetch_today_increment.sqlite3.connect") as mock_connect:
            mock_conn = mock_connect.return_value
            mock_conn.execute.return_value.fetchall.return_value = mock_rows
            result = get_stale_tickers("2026-10-10")
        assert "sh.600519" in result
        assert "sz.000001" in result
        assert len(result) == 2

    def test_empty_result(self) -> None:
        from scripts.fetch_today_increment import get_stale_tickers

        with patch("scripts.fetch_today_increment.sqlite3.connect") as mock_connect:
            mock_conn = mock_connect.return_value
            mock_conn.execute.return_value.fetchall.return_value = []
            result = get_stale_tickers("2026-10-10")
        assert result == []


class TestFillOne:
    """fill_one 单股填充测试。"""

    def test_success(self) -> None:
        from scripts.fetch_today_increment import fill_one

        old_data = _make_kline_pickle(10)
        new_df = pd.DataFrame(
            {
                "timestamps": ["2026-10-10"],
                "open": [110.0],
                "high": [111.0],
                "low": [109.0],
                "close": [110.5],
                "volume": [1_500_000],
            }
        )

        mock_provider = MagicMock()
        mock_result = MagicMock()
        mock_result.is_empty = False
        mock_result.to_dataframe.return_value = new_df
        mock_provider.fetch_kline.return_value = mock_result

        mock_factory = MagicMock()
        mock_factory.get_provider.return_value = mock_provider

        with patch("scripts.fetch_today_increment.sqlite3.connect") as mock_connect:
            mock_conn = mock_connect.return_value
            mock_conn.execute.return_value.fetchone.side_effect = [(old_data,), None]
            ticker, ok, msg = fill_one(mock_factory, "sh.600519", "2026-10-10")
        assert ok is True
        assert msg == "ok"

    def test_provider_not_found(self) -> None:
        from scripts.fetch_today_increment import fill_one

        mock_factory = MagicMock()
        mock_factory.get_provider.return_value = None
        ticker, ok, msg = fill_one(mock_factory, "sh.999999", "2026-10-10")
        assert ok is False
        assert msg == "no_provider"

    def test_empty_result(self) -> None:
        from scripts.fetch_today_increment import fill_one

        mock_provider = MagicMock()
        mock_result = MagicMock()
        mock_result.is_empty = True
        mock_provider.fetch_kline.return_value = mock_result

        mock_factory = MagicMock()
        mock_factory.get_provider.return_value = mock_provider

        ticker, ok, msg = fill_one(mock_factory, "sh.600519", "2026-10-10")
        assert ok is False
        assert msg == "empty"

    def test_exception_returns_error(self) -> None:
        from scripts.fetch_today_increment import fill_one

        mock_factory = MagicMock()
        mock_factory.get_provider.side_effect = RuntimeError("DB locked")
        ticker, ok, msg = fill_one(mock_factory, "sh.600519", "2026-10-10")
        assert ok is False
        assert "DB locked" in msg


class TestMain:
    """main 主流程测试。"""

    def test_same_day_noop(self) -> None:
        from scripts.fetch_today_increment import main

        today_str = pd.Timestamp.now().strftime("%Y-%m-%d")
        with (
            patch("scripts.fetch_today_increment.get_max_end_date", return_value=today_str),
            patch("scripts.fetch_today_increment.logger") as mock_logger,
        ):
            main()
            mock_logger.info.assert_called()
            from scripts import fetch_today_increment as mod
            with patch.object(mod, "get_stale_tickers", return_value=[]):
                main()
