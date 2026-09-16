"""测试 data_fetcher 模块：fetch_stock_data / fetch_stock_quote / prepare_kline_batch。"""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd

from trade_krono_cli.pipeline.data_fetcher import (
    fetch_stock_data,
    fetch_stock_quote,
    prepare_kline_batch,
)


class TestFetchStockData:
    """测试 fetch_stock_data。"""

    def test_normal_fetch(self) -> None:
        """正常拉取 K 线数据。"""
        df = pd.DataFrame(
            {
                "timestamps": pd.to_datetime(["2026-09-01", "2026-09-02"]),
                "open": [100.0, 101.0],
                "high": [102.0, 103.0],
                "low": [99.0, 100.0],
                "close": [101.0, 102.0],
                "volume": [1000, 1100],
                "amount": [100000, 110000],
            },
        )
        with patch(
            "trade_krono_cli.pipeline.data_fetcher.fetch_lookback",
            return_value=df,
        ) as mock_fetch:
            result = fetch_stock_data("sh.600519", "2026-09-02")
            assert len(result) == 2
            assert result["close"].tolist() == [101.0, 102.0]
            mock_fetch.assert_called_once_with(
                "sh.600519",
                "2026-09-02",
                lookback=400,
                frequency="d",
                adjustflag="1",
                use_cache=True,
            )

    def test_custom_params(self) -> None:
        """自定义参数传递正确。"""
        df = pd.DataFrame(
            {
                "timestamps": pd.to_datetime(["2026-09-01"]),
                "open": [50.0],
                "high": [51.0],
                "low": [49.0],
                "close": [50.5],
                "volume": [500],
                "amount": [50000],
            },
        )
        with patch(
            "trade_krono_cli.pipeline.data_fetcher.fetch_lookback",
            return_value=df,
        ) as mock_fetch:
            result = fetch_stock_data(
                "sz.000858",
                "2026-09-01",
                lookback=200,
                adjustflag="3",
                use_cache=False,
            )
            assert len(result) == 1
            mock_fetch.assert_called_once_with(
                "sz.000858",
                "2026-09-01",
                lookback=200,
                frequency="d",
                adjustflag="3",
                use_cache=False,
            )


class TestFetchStockQuote:
    """测试 fetch_stock_quote。"""

    def test_normal_quote(self) -> None:
        """正常获取实时行情。"""
        mock_quote = {"price": 1800.0, "pe": 28.5, "pb": 3.2}
        with patch(
            "trade_krono_cli.pipeline.data_fetcher.fetch_realtime_quote",
            return_value=mock_quote,
        ) as mock_fetch:
            result = fetch_stock_quote("sh.600519")
            assert result == mock_quote
            mock_fetch.assert_called_once_with("sh.600519")

    def test_empty_quote(self) -> None:
        """空行情返回空 dict。"""
        with patch(
            "trade_krono_cli.pipeline.data_fetcher.fetch_realtime_quote",
            return_value={},
        ):
            result = fetch_stock_quote("sh.600519")
            assert result == {}


class TestPrepareKlineBatch:
    """测试 prepare_kline_batch。"""

    def test_normal_batch(self) -> None:
        """正常批量获取 K 线。"""
        df = pd.DataFrame(
            {
                "timestamps": pd.to_datetime(["2026-09-01"]),
                "open": [100.0],
                "high": [102.0],
                "low": [99.0],
                "close": [101.0],
                "volume": [1000],
                "amount": [100000],
            },
        )
        with patch(
            "trade_krono_cli.pipeline.data_fetcher.fetch_stock_data",
            return_value=df,
        ) as mock_fetch:
            result = prepare_kline_batch(
                ["sh.600519", "sz.000858"],
                "2026-09-02",
                lookback=100,
            )
            assert len(result) == 2
            assert "sh.600519" in result
            assert "sz.000858" in result
            assert len(result["sh.600519"]) == 1
            assert mock_fetch.call_count == 2

    def test_partial_failure(self) -> None:
        """部分股票失败时返回成功部分，不抛出异常。"""
        from trade_krono_cli.errors import DataError

        df_ok = pd.DataFrame(
            {
                "timestamps": pd.to_datetime(["2026-09-01"]),
                "open": [100.0],
                "high": [102.0],
                "low": [99.0],
                "close": [101.0],
                "volume": [1000],
                "amount": [100000],
            },
        )
        with patch(
            "trade_krono_cli.pipeline.data_fetcher.fetch_stock_data",
            side_effect=[df_ok, DataError("no data"), Exception("unexpected")],
        ):
            result = prepare_kline_batch(
                ["sh.600519", "sz.000858", "sh.601318"],
                "2026-09-02",
            )
            # 第一个成功，第二、三个失败（被捕获）
            assert "sh.600519" in result
            # 失败的 ticker 不应出现在结果中
            assert "sz.000858" not in result
            assert "sh.601318" not in result

    def test_empty_tickers(self) -> None:
        """空 ticker 列表返回空 dict。"""
        result = prepare_kline_batch([], "2026-09-02")
        assert result == {}
