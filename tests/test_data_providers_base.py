"""trade_krono_cli.data_providers.base — 数据源抽象基类与标准化模型单元测试。

覆盖 KlineData / RealtimeQuote / StockMetadata 数据类，
以及 DataProvider 抽象基类的默认实现（健康检查、批量获取等）。
"""

from __future__ import annotations

from datetime import datetime

import pytest

from trade_krono_cli.data_providers.base import (
    DataProvider,
    KlineData,
    RealtimeQuote,
    StockMetadata,
)


class TestKlineData:
    def test_defaults(self) -> None:
        kd = KlineData()
        assert kd.length == 0
        assert kd.is_empty is True
        assert kd.timestamps == []
        assert kd.open == []

    def test_length_and_is_empty(self) -> None:
        kd = KlineData(
            timestamps=[datetime(2026, 8, 1)],
            open=[100.0],
            high=[103.0],
            low=[99.0],
            close=[101.0],
            volume=[1e6],
            amount=[1e8],
        )
        assert kd.length == 1
        assert kd.is_empty is False

    def test_to_dataframe(self) -> None:
        kd = KlineData(
            timestamps=[datetime(2026, 8, 1), datetime(2026, 8, 4)],
            open=[100.0, 102.0],
            high=[103.0, 104.0],
            low=[99.0, 101.0],
            close=[101.0, 103.0],
            volume=[1e6, 1.2e6],
            amount=[1e8, 1.2e8],
        )
        df = kd.to_dataframe()
        assert len(df) == 2
        assert list(df.columns) == [
            "timestamps",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "amount",
        ]

    def test_from_dataframe_roundtrip(self) -> None:
        import pandas as pd

        df = pd.DataFrame(
            {
                "timestamps": [datetime(2026, 8, 1), datetime(2026, 8, 4)],
                "open": [100.0, 102.0],
                "high": [103.0, 104.0],
                "low": [99.0, 101.0],
                "close": [101.0, 103.0],
                "volume": [1e6, 1.2e6],
                "amount": [1e8, 1.2e8],
            }
        )
        kd = KlineData.from_dataframe(df)
        assert kd.length == 2
        assert kd.close == [101.0, 103.0]


class TestRealtimeQuote:
    def test_defaults(self) -> None:
        q = RealtimeQuote()
        assert q.ticker == ""
        assert q.price is None
        assert q.source == ""

    def test_full_values(self) -> None:
        q = RealtimeQuote(
            ticker="sh.600519",
            name="贵州茅台",
            price=1800.5,
            pe=28.5,
            pb=5.2,
            market_cap=22600.0,
            turnover=0.3,
            source="baostock",
        )
        assert q.ticker == "sh.600519"
        assert q.name == "贵州茅台"
        assert q.price == 1800.5


class TestStockMetadata:
    def test_defaults(self) -> None:
        m = StockMetadata()
        assert m.ticker == ""
        assert m.industry is None
        assert m.is_st is False
        assert m.source == ""

    def test_full_values(self) -> None:
        m = StockMetadata(
            ticker="sh.600519",
            industry="白酒",
            industry_code="C16",
            pe_ttm=28.5,
            pb=5.2,
            ipo_date="1999-11-10",
            out_date=None,
            is_st=False,
            source="baostock",
        )
        assert m.ticker == "sh.600519"
        assert m.industry == "白酒"
        assert m.is_st is False


class TestDataProviderDefaultImpl:
    class _ConcreteProvider(DataProvider):
        name = "test_provider"

        def fetch_kline(self, ticker, start_date, end_date, frequency="d", adjustflag="1"):
            return None

        def fetch_quote(self, ticker):
            return None

        def fetch_metadata(self, ticker):
            return None

    def test_health_check_default_false_on_none(self) -> None:
        provider = self._ConcreteProvider()
        # fetch_metadata returns None → health_check False
        assert provider.health_check() is False

    def test_health_check_default_true_on_result(self) -> None:
        provider = self._ConcreteProvider()
        provider.fetch_metadata = lambda ticker: StockMetadata(ticker=ticker)
        assert provider.health_check() is True

    def test_fetch_quotes_batch_empty(self) -> None:
        provider = self._ConcreteProvider()
        assert provider.fetch_quotes_batch([]) == {}

    def test_fetch_quotes_batch_concurrent(self) -> None:
        provider = self._ConcreteProvider()

        def fake_quote(ticker):
            return RealtimeQuote(ticker=ticker, price=1.0)

        provider.fetch_quote = fake_quote  # type: ignore[method-assign]
        results = provider.fetch_quotes_batch(["sh.600519", "sz.000858", "bj.920001"])
        assert len(results) == 3
        assert results["sh.600519"].price == 1.0

    def test_fetch_metadata_batch_empty(self) -> None:
        provider = self._ConcreteProvider()
        assert provider.fetch_metadata_batch([]) == {}

    def test_fetch_metadata_batch_concurrent(self) -> None:
        provider = self._ConcreteProvider()

        def fake_meta(ticker):
            return StockMetadata(ticker=ticker, industry="银行")

        provider.fetch_metadata = fake_meta  # type: ignore[method-assign]
        results = provider.fetch_metadata_batch(["sh.600519", "sz.000858"])
        assert len(results) == 2
        assert results["sh.600519"].industry == "银行"

    def test_fetch_kline_as_df_none(self) -> None:
        provider = self._ConcreteProvider()
        assert provider.fetch_kline_as_df("sh.600519", "2026-01-01", "2026-01-02") is None

    def test_fetch_kline_as_df_dataframe(self) -> None:
        provider = self._ConcreteProvider()
        provider.fetch_kline = lambda *a, **k: KlineData(  # type: ignore[method-assign]
            timestamps=[datetime(2026, 8, 1)],
            open=[100.0],
            high=[103.0],
            low=[99.0],
            close=[101.0],
            volume=[1e6],
            amount=[1e8],
        )
        df = provider.fetch_kline_as_df("sh.600519", "2026-08-01", "2026-08-04")
        assert df is not None
        assert len(df) == 1

    def test_abstract_cannot_instantiate(self) -> None:
        with pytest.raises(TypeError):
            DataProvider()  # type: ignore[abstract]
