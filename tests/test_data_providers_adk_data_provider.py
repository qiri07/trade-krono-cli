"""trade_krono_cli.data_providers.adk_data_provider — a-stock-data (adata) 兜底源单元测试。

由于 a-stock-data 可能未安装，测试通过 monkeypatch `_adk` 类属性和
`_ensure_import` 来模拟数据源返回，不依赖真实网络或第三方包。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from trade_krono_cli.data_providers.adk_data_provider import AdkDataProvider


@pytest.fixture(autouse=True)
def _reset_adk_class_attr() -> None:
    """在每个测试前后重置 AdkDataProvider._adk 类属性，避免跨测试污染。"""
    AdkDataProvider._adk = None
    yield
    AdkDataProvider._adk = None


def _make_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "timestamps": ["2026-08-01", "2026-08-04"],
            "开盘": [100.0, 102.0],
            "最高": [103.0, 104.0],
            "最低": [99.0, 101.0],
            "收盘": [101.0, 103.0],
            "成交量": [1e6, 1.2e6],
            "成交额": [1e8, 1.2e8],
        }
    )


def _make_spot_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "代码": ["600519"],
            "最新价": [1800.5],
            "市盈率-动态": [28.5],
            "市净率": [5.2],
            "总市值": [22600.0],
            "换手率": [0.3],
            "行业": ["白酒"],
        }
    )


class TestAdkDataProviderTickerConversion:
    def test_ticker_to_adk_strips_prefix(self) -> None:
        assert AdkDataProvider._ticker_to_adk("sh.600519") == "600519"
        assert AdkDataProvider._ticker_to_adk("sz.000858") == "000858"
        assert AdkDataProvider._ticker_to_adk("bj.920001") == "920001"

    def test_adk_to_ticker_sh_prefix(self) -> None:
        assert AdkDataProvider._adk_to_ticker("600519") == "sh.600519"
        assert AdkDataProvider._adk_to_ticker("510300") == "sh.510300"

    def test_adk_to_ticker_sz_prefix(self) -> None:
        assert AdkDataProvider._adk_to_ticker("000858") == "sz.000858"
        assert AdkDataProvider._adk_to_ticker("159915") == "sz.159915"


class TestAdkDataProviderFetchKline:
    def _patch_provider(self, df: pd.DataFrame | None) -> AdkDataProvider:
        provider = AdkDataProvider()
        mock_adk = MagicMock()
        mock_adk.stock_hist_daily.return_value = df
        AdkDataProvider._adk = mock_adk
        return provider

    def test_fetch_kline_success(self) -> None:
        provider = self._patch_provider(_make_df())
        data = provider.fetch_kline("sh.600519", "2026-08-01", "2026-08-04")
        assert data is not None
        assert data.length == 2
        assert data.close == [101.0, 103.0]
        assert data.open == [100.0, 102.0]

    def test_fetch_kline_empty_returns_none(self) -> None:
        provider = self._patch_provider(pd.DataFrame())
        assert provider.fetch_kline("sh.600519", "2026-08-01", "2026-08-04") is None

    def test_fetch_kline_none_returns_none(self) -> None:
        provider = self._patch_provider(None)
        assert provider.fetch_kline("sh.600519", "2026-08-01", "2026-08-04") is None

    def test_fetch_kline_missing_required_column_returns_none(self) -> None:
        provider = self._patch_provider(pd.DataFrame({"date": ["2026-08-01"]}))
        assert provider.fetch_kline("sh.600519", "2026-08-01", "2026-08-04") is None

    def test_fetch_kline_import_error_propagates(self) -> None:
        provider = AdkDataProvider()
        with patch.object(
            AdkDataProvider, "_ensure_import", side_effect=ImportError("no adk")
        ), pytest.raises(ImportError):
            provider.fetch_kline("sh.600519", "2026-08-01", "2026-08-04")


class TestAdkDataProviderFetchQuote:
    def _patch_provider(self, df: pd.DataFrame | None) -> AdkDataProvider:
        provider = AdkDataProvider()
        mock_adk = MagicMock()
        mock_adk.stock_zh_a_spot_em.return_value = df
        AdkDataProvider._adk = mock_adk
        return provider

    def test_fetch_quote_success(self) -> None:
        provider = self._patch_provider(_make_spot_df())
        quote = provider.fetch_quote("sh.600519")
        assert quote is not None
        assert quote.ticker == "sh.600519"
        assert quote.price == 1800.5
        assert quote.pe == 28.5
        assert quote.pb == 5.2
        assert quote.market_cap == 22600.0
        assert quote.turnover == 0.3
        assert quote.source == "adata"

    def test_fetch_quote_empty_returns_none(self) -> None:
        provider = self._patch_provider(pd.DataFrame())
        assert provider.fetch_quote("sh.600519") is None

    def test_fetch_quote_none_returns_none(self) -> None:
        provider = self._patch_provider(None)
        assert provider.fetch_quote("sh.600519") is None


class TestAdkDataProviderFetchMetadata:
    def _patch_provider(self, df: pd.DataFrame | None) -> AdkDataProvider:
        provider = AdkDataProvider()
        mock_adk = MagicMock()
        mock_adk.stock_zh_a_spot_em.return_value = df
        AdkDataProvider._adk = mock_adk
        return provider

    def test_fetch_metadata_success(self) -> None:
        provider = self._patch_provider(_make_spot_df())
        meta = provider.fetch_metadata("sh.600519")
        assert meta is not None
        assert meta.ticker == "sh.600519"
        assert meta.industry == "白酒"
        assert meta.pe_ttm == 28.5
        assert meta.pb == 5.2
        assert meta.source == "adata"

    def test_fetch_metadata_empty_returns_none(self) -> None:
        provider = self._patch_provider(pd.DataFrame())
        assert provider.fetch_metadata("sh.600519") is None


class TestAdkDataProviderFetchQuotesBatch:
    def test_fetch_quotes_batch_empty(self) -> None:
        provider = AdkDataProvider()
        assert provider.fetch_quotes_batch([]) == {}

    def test_fetch_quotes_batch_success(self) -> None:
        provider = AdkDataProvider()
        mock_adk = MagicMock()
        mock_adk.stock_zh_a_spot_em.return_value = _make_spot_df()
        AdkDataProvider._adk = mock_adk
        results = provider.fetch_quotes_batch(["sh.600519"])
        assert "sh.600519" in results
        assert results["sh.600519"].price == 1800.5

    def test_fetch_quotes_batch_missing_ticker(self) -> None:
        provider = AdkDataProvider()
        mock_adk = MagicMock()
        mock_adk.stock_zh_a_spot_em.return_value = _make_spot_df()
        AdkDataProvider._adk = mock_adk
        results = provider.fetch_quotes_batch(["sz.999999"])
        assert results == {}


class TestAdkDataProviderFetchMetadataBatch:
    def test_fetch_metadata_batch_empty(self) -> None:
        provider = AdkDataProvider()
        assert provider.fetch_metadata_batch([]) == {}

    def test_fetch_metadata_batch_success(self) -> None:
        provider = AdkDataProvider()
        mock_adk = MagicMock()
        mock_adk.stock_zh_a_spot_em.return_value = _make_spot_df()
        AdkDataProvider._adk = mock_adk
        results = provider.fetch_metadata_batch(["sh.600519"])
        assert "sh.600519" in results
        assert results["sh.600519"].industry == "白酒"


class TestAdkDataProviderHealthCheck:
    def test_health_check_true(self) -> None:
        provider = AdkDataProvider()
        mock_adk = MagicMock()
        mock_adk.stock_zh_a_hist.return_value = _make_df()
        AdkDataProvider._adk = mock_adk
        assert provider.health_check() is True

    def test_health_check_false_empty(self) -> None:
        provider = AdkDataProvider()
        mock_adk = MagicMock()
        mock_adk.stock_zh_a_hist.return_value = pd.DataFrame()
        AdkDataProvider._adk = mock_adk
        assert provider.health_check() is False

    def test_health_check_exception_returns_false(self) -> None:
        provider = AdkDataProvider()
        mock_adk = MagicMock()
        mock_adk.stock_zh_a_hist.side_effect = RuntimeError("boom")
        AdkDataProvider._adk = mock_adk
        assert provider.health_check() is False
