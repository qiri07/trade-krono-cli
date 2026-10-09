"""tests/test_akshare_hk_provider.py — AkShare 港股 Provider（多源备选）单元测试。"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from trade_krono_cli.data_providers.akshare_hk_provider import AkShareHKProvider
from trade_krono_cli.data_providers.base import KlineData


class TestAkShareHKProvider:
    """AkShareHKProvider 单元测试。"""

    @pytest.fixture(autouse=True)
    def _reset_provider(self) -> None:
        """测试前后重置 provider 状态。"""
        AkShareHKProvider._ak = None
        AkShareHKProvider._req = None
        yield
        AkShareHKProvider._ak = None
        AkShareHKProvider._req = None

    def test_ticker_to_symbol(self) -> None:
        """ticker 转换为纯数字代码。"""
        assert AkShareHKProvider._ticker_to_symbol("hk.00700") == "00700"
        assert AkShareHKProvider._ticker_to_symbol("hk.09988") == "09988"
        assert AkShareHKProvider._ticker_to_symbol("hk.00941") == "00941"

    def test_provider_attributes(self) -> None:
        """provider 类属性。"""
        p = AkShareHKProvider()
        assert p.name == "akshare_hk"
        assert p.supports_kline is True
        assert p.supports_quote is True
        assert p.supports_metadata is True

    def test_unsupported_frequency(self) -> None:
        """不支持的频率返回 None。"""
        with patch.object(AkShareHKProvider, "_ensure_import"):
            provider = AkShareHKProvider()
            result = provider.fetch_kline("hk.00700", frequency="w")
            assert result is None

    def test_import_error(self) -> None:
        """akshare 未安装时 _ensure_import 抛出 RuntimeError。"""
        with patch.object(
            AkShareHKProvider, "_ensure_import", side_effect=RuntimeError("akshare 未安装")
        ):
            provider = AkShareHKProvider()
            result = provider.fetch_kline("hk.00700")
            assert result is None

    @patch.object(AkShareHKProvider, "_ensure_import")
    def test_fetch_kline_main_source_success(self, mock_ensure: MagicMock) -> None:
        """主源 akshare_daily 成功拉取数据。"""
        mock_ak = MagicMock()
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
            result = provider.fetch_kline(
                "hk.00700", start_date="2026-01-01", end_date="2026-10-03"
            )

        assert result is not None
        assert isinstance(result, KlineData)
        assert result.length == 1
        assert result.close[0] == 101.0
        mock_ak.stock_hk_daily.assert_called_once_with(symbol="00700", adjust="qfq")

    @patch.object(AkShareHKProvider, "_ensure_import")
    def test_fetch_kline_main_source_fallback_to_sina(self, mock_ensure: MagicMock) -> None:
        """主源失败时回退到 sina_direct。"""
        mock_ak = MagicMock()
        mock_ak.stock_hk_daily.side_effect = Exception("network error")

        with patch.object(AkShareHKProvider, "_ak", mock_ak):
            provider = AkShareHKProvider()
            # _fetch_via_sina_direct 也需要 _req，这里直接 mock
            with patch.object(AkShareHKProvider, "_fetch_via_sina_direct", return_value=None):
                with patch.object(
                    AkShareHKProvider, "_fetch_via_tencent_minute", return_value=None
                ):
                    result = provider.fetch_kline("hk.00700")
                    assert result is None

    @patch.object(AkShareHKProvider, "_ensure_import")
    def test_fetch_kline_empty_df(self, mock_ensure: MagicMock) -> None:
        """空 DataFrame 返回 None。"""
        mock_ak = MagicMock()
        mock_ak.stock_hk_daily.return_value = pd.DataFrame()

        with patch.object(AkShareHKProvider, "_ak", mock_ak):
            provider = AkShareHKProvider()
            result = provider.fetch_kline("hk.00700")

        assert result is None

    def test_fetch_metadata_returns_none(self) -> None:
        """fetch_metadata 返回 None 当 API 调用失败时。"""
        p = AkShareHKProvider()
        with patch("trade_krono_cli.data_providers.akshare_hk_provider.requests.get") as mock_get:
            mock_get.side_effect = Exception("network error")
            assert p.fetch_metadata("hk.00700") is None

    @patch.object(AkShareHKProvider, "_ensure_import")
    def test_health_check_main_source_ok(self, mock_ensure: MagicMock) -> None:
        """主源可用时 health_check 返回 True。"""
        mock_ak = MagicMock()
        mock_ak.stock_hk_daily.return_value = pd.DataFrame({"date": ["2026-10-02"]})

        with patch.object(AkShareHKProvider, "_ak", mock_ak):
            provider = AkShareHKProvider()
            assert provider.health_check() is True

    @patch.object(AkShareHKProvider, "_ensure_import")
    def test_health_check_all_sources_fail(self, mock_ensure: MagicMock) -> None:
        """所有源都失败时 health_check 返回 False。"""
        mock_ensure.side_effect = Exception("network down")
        provider = AkShareHKProvider()
        assert provider.health_check() is False

    @patch.object(AkShareHKProvider, "_ensure_import")
    def test_fetch_quote_success(self, mock_ensure: MagicMock) -> None:
        """实时行情获取成功。"""
        mock_ak = MagicMock()
        fake_spot = pd.DataFrame(
            [
                {
                    "代码": "00700",
                    "最新价": 421.2,
                    "昨收": 420.5,
                    "成交量": 3635907.0,
                    "成交额": 1.5e9,
                }
            ]
        )
        mock_ak.stock_hk_spot.return_value = fake_spot

        with patch.object(AkShareHKProvider, "_ak", mock_ak):
            provider = AkShareHKProvider()
            result = provider.fetch_quote("hk.00700")

        assert result is not None
        assert result.ticker == "hk.00700"
        assert result.price == 421.2

    @patch.object(AkShareHKProvider, "_ensure_import")
    def test_fetch_quote_not_found(self, mock_ensure: MagicMock) -> None:
        """股票不在 spot 列表中返回 None。"""
        mock_ak = MagicMock()
        fake_spot = pd.DataFrame([{"代码": "99999", "最新价": 10.0}])
        mock_ak.stock_hk_spot.return_value = fake_spot

        with patch.object(AkShareHKProvider, "_ak", mock_ak):
            provider = AkShareHKProvider()
            result = provider.fetch_quote("hk.00700")
            assert result is None

    @patch.object(AkShareHKProvider, "_fetch_via_akshare_daily")
    @patch.object(AkShareHKProvider, "_fetch_via_sina_direct")
    @patch.object(AkShareHKProvider, "_fetch_via_tencent_minute")
    def test_fetch_kline_multi_source_chain(
        self,
        mock_tencent: MagicMock,
        mock_sina: MagicMock,
        mock_daily: MagicMock,
    ) -> None:
        """多源链式回退：主源→备用1→备用2。"""
        # 主源返回 None
        mock_daily.return_value = None
        # 备用1返回 None
        mock_sina.return_value = None
        # 备用2（腾讯）成功
        mock_tencent.return_value = pd.DataFrame(
            {
                "date": [datetime(2026, 10, 5)],
                "open": [421.0],
                "high": [422.0],
                "low": [420.0],
                "close": [421.5],
                "volume": [100000.0],
            }
        )

        provider = AkShareHKProvider()
        result = provider.fetch_kline("hk.00700")

        assert result is not None
        assert result.close[0] == 421.5
        mock_daily.assert_called_once_with("00700", "1")
        mock_sina.assert_called_once_with("00700")
        mock_tencent.assert_called_once_with("00700")

    @patch.object(AkShareHKProvider, "_fetch_via_akshare_daily")
    def test_fetch_kline_date_filtering(self, mock_daily: MagicMock) -> None:
        """日期范围过滤正确生效。"""
        mock_daily.return_value = pd.DataFrame(
            {
                "date": [
                    datetime(2026, 9, 1),
                    datetime(2026, 9, 15),
                    datetime(2026, 10, 2),
                ],
                "open": [100.0, 105.0, 110.0],
                "high": [101.0, 106.0, 111.0],
                "low": [99.0, 104.0, 109.0],
                "close": [100.5, 105.5, 110.5],
                "volume": [1e6, 1.1e6, 1.2e6],
                "amount": [1e8, 1.1e8, 1.2e8],
            }
        )

        provider = AkShareHKProvider()
        result = provider.fetch_kline("hk.00700", start_date="2026-09-10", end_date="2026-10-03")

        assert result is not None
        assert result.length == 2
        assert result.timestamps[0].date() == datetime(2026, 9, 15).date()
        assert result.timestamps[1].date() == datetime(2026, 10, 2).date()
