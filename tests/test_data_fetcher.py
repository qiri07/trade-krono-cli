"""测试 pipeline.data_fetcher 模块。"""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd


def _make_df(rows: int = 400) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "timestamps": pd.date_range("2025-01-01", periods=rows, freq="B"),
            "open": [100.0] * rows,
            "high": [101.0] * rows,
            "low": [99.0] * rows,
            "close": [100.5] * rows,
            "volume": [1e6] * rows,
            "amount": [1e8] * rows,
        }
    )


class TestFetchStockData:
    """fetch_stock_data 单元测试。"""

    def test_normal_call(self):
        """正常调用应调用 fetch_lookback 并返回 DataFrame。"""
        from trade_krono_cli.pipeline.data_fetcher import fetch_stock_data

        mock_df = _make_df(400)
        with patch(
            "trade_krono_cli.pipeline.data_fetcher.fetch_lookback", return_value=mock_df
        ) as mock_fetch:
            result = fetch_stock_data("sh.600519", "2026-08-12")

        assert isinstance(result, pd.DataFrame)
        assert len(result) == 400
        mock_fetch.assert_called_once_with(
            "sh.600519",
            "2026-08-12",
            lookback=400,
            frequency="d",
            adjustflag="1",
            use_cache=True,
        )

    def test_custom_parameters(self):
        """自定义参数应透传给 fetch_lookback。"""
        from trade_krono_cli.pipeline.data_fetcher import fetch_stock_data

        mock_df = _make_df(300)
        with patch(
            "trade_krono_cli.pipeline.data_fetcher.fetch_lookback", return_value=mock_df
        ) as mock_fetch:
            fetch_stock_data(
                "sz.000858", "2026-08-12", lookback=300, adjustflag="3", use_cache=False
            )

        mock_fetch.assert_called_once_with(
            "sz.000858",
            "2026-08-12",
            lookback=300,
            frequency="d",
            adjustflag="3",
            use_cache=False,
        )

    def test_returns_correct_columns(self):
        """返回的 DataFrame 应有正确的列名。"""
        from trade_krono_cli.pipeline.data_fetcher import fetch_stock_data

        mock_df = _make_df(100)
        with patch("trade_krono_cli.pipeline.data_fetcher.fetch_lookback", return_value=mock_df):
            result = fetch_stock_data("sh.600519", "2026-08-12")

        expected_cols = {"timestamps", "open", "high", "low", "close", "volume", "amount"}
        assert expected_cols.issubset(set(result.columns))


class TestFetchStockQuote:
    """fetch_stock_quote 单元测试。"""

    def test_returns_quote_dict(self):
        """应调用 fetch_realtime_quote 并返回结果。"""
        from trade_krono_cli.pipeline.data_fetcher import fetch_stock_quote

        mock_quote = {"price": 1680.0, "pe": 35.6, "pb": 18.2}
        with patch(
            "trade_krono_cli.pipeline.data_fetcher.fetch_realtime_quote", return_value=mock_quote
        ) as mock_fetch:
            result = fetch_stock_quote("sh.600519")

        assert result == mock_quote
        mock_fetch.assert_called_once_with("sh.600519")

    def test_returns_empty_dict_on_empty_quote(self):
        """fetch_realtime_quote 返回空 dict 时应原样返回。"""
        from trade_krono_cli.pipeline.data_fetcher import fetch_stock_quote

        with patch(
            "trade_krono_cli.pipeline.data_fetcher.fetch_realtime_quote", return_value={}
        ) as mock_fetch:
            result = fetch_stock_quote("sh.600519")

        assert result == {}
        mock_fetch.assert_called_once()


class TestPrepareKlineBatch:
    """prepare_kline_batch 单元测试。"""

    def test_single_ticker(self):
        """单只股票应返回包含该股票数据的字典。"""
        from trade_krono_cli.pipeline.data_fetcher import prepare_kline_batch

        mock_df = _make_df(400)
        with patch(
            "trade_krono_cli.pipeline.data_fetcher.fetch_stock_data", return_value=mock_df
        ) as mock_fetch:
            result = prepare_kline_batch(["sh.600519"], "2026-08-12")

        assert "sh.600519" in result
        assert len(result["sh.600519"]) == 400
        mock_fetch.assert_called_once()

    def test_multiple_tickers(self):
        """多只股票应逐一处理。"""
        from trade_krono_cli.pipeline.data_fetcher import prepare_kline_batch

        mock_df = _make_df(400)
        with patch(
            "trade_krono_cli.pipeline.data_fetcher.fetch_stock_data", return_value=mock_df
        ) as mock_fetch:
            result = prepare_kline_batch(["sh.600519", "sz.000858", "sh.600036"], "2026-08-12")

        assert len(result) == 3
        assert mock_fetch.call_count == 3

    def test_empty_list_returns_empty_dict(self):
        """空列表应返回空字典。"""
        from trade_krono_cli.pipeline.data_fetcher import prepare_kline_batch

        result = prepare_kline_batch([], "2026-08-12")
        assert result == {}

    def test_failed_ticker_skipped(self):
        """单只股票失败不应中断其他股票的处理。"""
        from trade_krono_cli.errors import DataError
        from trade_krono_cli.pipeline.data_fetcher import prepare_kline_batch

        mock_df = _make_df(400)
        with patch("trade_krono_cli.pipeline.data_fetcher.fetch_stock_data") as mock_fetch:
            mock_fetch.side_effect = [
                mock_df,  # sh.600519 成功
                DataError("缓存不可用"),  # sz.000858 失败
                mock_df,  # sh.600036 成功
            ]
            result = prepare_kline_batch(["sh.600519", "sz.000858", "sh.600036"], "2026-08-12")

        assert "sh.600519" in result
        assert "sz.000858" not in result  # 失败的不包含在结果中
        assert "sh.600036" in result
        assert len(result) == 2

    def test_generic_exception_skipped(self):
        """通用异常（非 DataError）也应被捕获并跳过。"""
        from trade_krono_cli.pipeline.data_fetcher import prepare_kline_batch

        mock_df = _make_df(400)
        with patch("trade_krono_cli.pipeline.data_fetcher.fetch_stock_data") as mock_fetch:
            mock_fetch.side_effect = [
                mock_df,  # sh.600519 成功
                RuntimeError("unexpected error"),  # 通用异常
                mock_df,  # sh.600036 成功
            ]
            result = prepare_kline_batch(["sh.600519", "sz.000858", "sh.600036"], "2026-08-12")

        assert "sh.600519" in result
        assert "sz.000858" not in result
        assert "sh.600036" in result
        assert len(result) == 2

    def test_default_parameters(self):
        """默认参数应使用 lookback=400, adjustflag="1", use_cache=True。"""
        from trade_krono_cli.pipeline.data_fetcher import prepare_kline_batch

        mock_df = _make_df(400)
        with patch(
            "trade_krono_cli.pipeline.data_fetcher.fetch_stock_data", return_value=mock_df
        ) as mock_fetch:
            prepare_kline_batch(["sh.600519"], "2026-08-12")

        _, kwargs = mock_fetch.call_args
        assert kwargs["lookback"] == 400
        assert kwargs["adjustflag"] == "1"
        assert kwargs["use_cache"] is True

    def test_custom_parameters_passed_through(self):
        """自定义参数应正确透传给 fetch_stock_data。"""
        from trade_krono_cli.pipeline.data_fetcher import prepare_kline_batch

        mock_df = _make_df(500)
        with patch(
            "trade_krono_cli.pipeline.data_fetcher.fetch_stock_data", return_value=mock_df
        ) as mock_fetch:
            prepare_kline_batch(
                ["sh.600519"],
                "2026-08-12",
                lookback=500,
                adjustflag="3",
                use_cache=False,
            )

        _, kwargs = mock_fetch.call_args
        assert kwargs["lookback"] == 500
        assert kwargs["adjustflag"] == "3"
        assert kwargs["use_cache"] is False
