"""tests for trade_krono_cli.data_providers.easyquotation_provider (easyquotation 免费实时行情源)。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from trade_krono_cli.data_providers.easyquotation_provider import (
    EasyQuotationProvider,
)


class TestEasyQuotationProvider:
    @pytest.fixture
    def provider(self):
        return EasyQuotationProvider()

    def test_name_and_capabilities(self, provider) -> None:
        assert provider.name == "easyquotation"
        assert provider.supports_kline is False
        assert provider.supports_quote is True
        assert provider.supports_metadata is False

    def test_ticker_to_code(self, provider) -> None:
        """带前缀 ticker 转 6 位裸代码；非法格式返回 None。"""
        assert provider._ticker_to_code("sh.600519") == "600519"
        assert provider._ticker_to_code("sz.000858") == "000858"
        assert provider._ticker_to_code("bj.920000") == "920000"
        assert provider._ticker_to_code("600519") == "600519"
        assert provider._ticker_to_code("hk.00700") is None
        assert provider._ticker_to_code("invalid") is None

    def test_fetch_kline_not_supported(self, provider) -> None:
        """easyquotation 不支持 K 线，始终返回 None。"""
        result = provider.fetch_kline("sh.600519", "2026-01-01", "2026-08-13")
        assert result is None

    def test_fetch_metadata_not_supported(self, provider) -> None:
        """easyquotation 不提供完整基本面，始终返回 None。"""
        result = provider.fetch_metadata("sh.600519")
        assert result is None

    def test_fetch_quote_import_error(self, provider) -> None:
        with patch.object(
            EasyQuotationProvider,
            "_ensure_import",
            side_effect=RuntimeError("easyquotation not installed"),
        ), pytest.raises(RuntimeError, match="easyquotation not installed"):
            provider.fetch_quote("sh.600519")

    def test_fetch_quote_tencent_source(self, provider) -> None:
        """腾讯源实时行情：提取 now/PE/PB/总市值/turnover。"""
        data = {
            "600519": {
                "name": "贵州茅台",
                "code": "600519",
                "now": 1451.79,
                "PE": 22.09,
                "PB": 6.69,
                "总市值": 18148.56,
                "turnover": 0.23,
            }
        }

        mock_eq = MagicMock()
        mock_source = MagicMock()
        mock_source.real.return_value = data
        mock_eq.use.return_value = mock_source
        EasyQuotationProvider._eq = mock_eq
        EasyQuotationProvider._source = "tencent"

        result = provider.fetch_quote("sh.600519")
        assert result is not None
        assert result.ticker == "sh.600519"
        assert result.price == 1451.79
        assert result.pe == 22.09
        assert result.pb == 6.69
        assert result.market_cap == 18148.56
        assert result.turnover == 0.23
        assert result.source == "easyquotation:tencent"

        mock_eq.use.assert_called_once_with("tencent")
        mock_source.real.assert_called_once_with(["600519"])

    def test_fetch_quote_sina_source(self, provider) -> None:
        """新浪源实时行情：字段名不同（now, 无 PE/PB），应容错。"""
        data = {
            "600519": {
                "name": "贵州茅台",
                "now": 1451.79,
                "open": 1450.0,
                "high": 1458.0,
                "low": 1435.1,
                "volume": 4101175355.0,
            }
        }

        mock_eq = MagicMock()
        mock_source = MagicMock()
        mock_source.real.return_value = data
        mock_eq.use.return_value = mock_source
        EasyQuotationProvider._eq = mock_eq
        EasyQuotationProvider._source = "sina"

        result = provider.fetch_quote("sh.600519")
        assert result is not None
        assert result.price == 1451.79
        assert result.pe is None  # 新浪源无 PE 字段
        assert result.pb is None

    def test_fetch_quote_empty_result(self, provider) -> None:
        """行情源返回空时应返回 None。"""
        mock_eq = MagicMock()
        mock_source = MagicMock()
        mock_source.real.return_value = {}
        mock_eq.use.return_value = mock_source
        EasyQuotationProvider._eq = mock_eq
        EasyQuotationProvider._source = "tencent"

        result = provider.fetch_quote("sh.600519")
        assert result is None
