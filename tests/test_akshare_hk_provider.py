"""tests/test_akshare_hk_provider.py — AkShare 港股 Provider 单元测试。"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from trade_krono_cli.data_providers.akshare_hk_provider import AkShareHKProvider


class TestAkShareHKProvider:
    """AkShareHKProvider 单元测试。"""

    @pytest.fixture(autouse=True)
    def _reset_provider(self) -> None:
        """测试前后重置 provider 状态。"""
        AkShareHKProvider._ak = None
        yield
        AkShareHKProvider._ak = None

    def test_ticker_to_symbol(self) -> None:
        """ticker 转换为 akshare 符号。"""
        assert AkShareHKProvider._ticker_to_symbol("hk.00700") == "00700"
        assert AkShareHKProvider._ticker_to_symbol("hk.09988") == "09988"
        assert AkShareHKProvider._ticker_to_symbol("hk.00941") == "00941"

    def test_provider_attributes(self) -> None:
        """provider 类属性。"""
        p = AkShareHKProvider()
        assert p.name == "akshare_hk"
        assert p.supports_kline is True
        assert p.supports_quote is False
        assert p.supports_metadata is False

    def test_fetch_quote_returns_none(self) -> None:
        """fetch_quote 返回 None。"""
        p = AkShareHKProvider()
        assert p.fetch_quote("hk.00700") is None

    def test_fetch_metadata_returns_none(self) -> None:
        """fetch_metadata 返回 None。"""
        p = AkShareHKProvider()
        assert p.fetch_metadata("hk.00700") is None

    def test_unsupported_frequency(self) -> None:
        """不支持的频率返回 None。"""
        with patch.object(AkShareHKProvider, "_ensure_import"):
            provider = AkShareHKProvider()
            result = provider.fetch_kline("hk.00700", frequency="w")
            assert result is None

    def test_import_error(self) -> None:
        """akshare 未安装时 _ensure_import 内部捕获 ImportError 并转抛 RuntimeError。"""
        AkShareHKProvider._ak = None
        # 直接模拟 _ensure_import 的最终行为：抛出 RuntimeError
        with patch.object(AkShareHKProvider, "_ensure_import", side_effect=RuntimeError("akshare 未安装")):
            provider = AkShareHKProvider()
            # fetch_kline 的 except Exception 捕获 RuntimeError，返回 None
            result = provider.fetch_kline("hk.00700")
            assert result is None

    @patch.object(AkShareHKProvider, "_ensure_import")
    def test_fetch_kline_exception_returns_none(self, mock_ensure: MagicMock) -> None:
        """网络异常时返回 None 而非崩溃。"""
        mock_ak = MagicMock()
        mock_ak.stock_hk_daily.side_effect = Exception("connection refused")

        with patch.object(AkShareHKProvider, "_ak", mock_ak):
            provider = AkShareHKProvider()
            result = provider.fetch_kline("hk.00700")
            assert result is None

    @patch.object(AkShareHKProvider, "_ensure_import")
    def test_fetch_kline_success(self, mock_ensure: MagicMock) -> None:
        """成功拉取港股 K 线数据。"""
        from trade_krono_cli.data_providers.base import KlineData

        mock_ak = MagicMock()

        # 构造符合 akshare stock_hk_daily 返回格式的 DataFrame
        import pandas as pd

        fake_df = pd.DataFrame(
            {
                "date": [datetime(2026, 10, 2)],
                "open": [100.0],
                "high": [102.0],
                "low": [99.0],
                "close": [101.0],
                "volume": [1000000.0],
                "amount": [1e8],
            },
        )
        mock_ak.stock_hk_daily.return_value = fake_df

        with patch.object(AkShareHKProvider, "_ak", mock_ak):
            provider = AkShareHKProvider()
            result = provider.fetch_kline("hk.00700", start_date="2026-01-01", end_date="2026-10-03")

        assert result is not None
        assert isinstance(result, KlineData)
        assert result.length == 1
        assert result.close[0] == 101.0
        mock_ak.stock_hk_daily.assert_called_once_with(symbol="00700", adjust="1")

    @patch.object(AkShareHKProvider, "_ensure_import")
    def test_fetch_kline_empty_df(self, mock_ensure: MagicMock) -> None:
        """空 DataFrame 返回 None。"""
        mock_ak = MagicMock()
        mock_ak.stock_hk_daily.return_value = None

        with patch.object(AkShareHKProvider, "_ak", mock_ak):
            provider = AkShareHKProvider()
            result = provider.fetch_kline("hk.00700")

        assert result is None

    def test_health_check_failure(self) -> None:
        """健康检查失败时返回 False。"""
        with patch.object(AkShareHKProvider, "_ensure_import") as mock_ensure:
            mock_ensure.side_effect = Exception("network down")
            provider = AkShareHKProvider()
            assert provider.health_check() is False
