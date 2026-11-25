"""测试 MootDxProvider — 同花顺 Moomoo 数据源（所有调用均 mock，无需真实 mootdx 包）。

覆盖：
- MootDxProvider 类属性与 _ticker_to_mootdx（sh/sz/bj 前缀）
- _ensure_client（已加载、ImportError、ConnectionError、一般 Exception）
- _estimate_bars（各频率、日期解析异常）
- _fetch_bars_in_range（分页、空 df、日期覆盖、无更多数据、合并去重）
- fetch_kline（正常、bj 返回 None、空 df、API 异常、日期过滤为空、无 datetime 列）
- fetch_quote（正常、空列表、异常）
- fetch_metadata（始终返回 None）
- health_check（成功、空结果、异常）
"""

from __future__ import annotations

import types
from datetime import datetime
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from trade_krono_cli.data_providers.mootdx_provider import MootDxProvider


@pytest.fixture
def provider() -> MootDxProvider:
    return MootDxProvider()


def _reset_class_state() -> None:
    MootDxProvider._client = None
    MootDxProvider._connected = False


class TestMootDxProviderClassAttrs:
    """MootDxProvider 类属性测试。"""

    def test_name(self, provider: MootDxProvider) -> None:
        assert provider.name == "mootdx"
        assert provider.supports_kline is True
        assert provider.supports_quote is True
        assert provider.supports_metadata is False


class TestTickerConversion:
    """_ticker_to_mootdx 静态方法测试。"""

    def test_sh_prefix(self, provider: MootDxProvider) -> None:
        market, code = provider._ticker_to_mootdx("sh.600519")
        assert market == 1
        assert code == "600519"

    def test_sz_prefix(self, provider: MootDxProvider) -> None:
        market, code = provider._ticker_to_mootdx("sz.000001")
        assert market == 0
        assert code == "000001"

    def test_bj_prefix_returns_minus_one(self, provider: MootDxProvider) -> None:
        market, code = provider._ticker_to_mootdx("bj.920001")
        assert market == -1
        assert code == "920001"


class TestEnsureClient:
    """_ensure_client 方法测试。"""

    def test_ensure_client_already_connected(self, provider: MootDxProvider) -> None:
        """_client 且 _connected 时不应重复连接。"""
        _reset_class_state()
        mock_client = MagicMock()
        MootDxProvider._client = mock_client
        MootDxProvider._connected = True
        with patch("builtins.__import__") as mock_import:
            provider._ensure_client()
            mock_import.assert_not_called()
        _reset_class_state()

    def test_ensure_client_import_error(self, provider: MootDxProvider) -> None:
        """mootdx 未安装时应抛出 RuntimeError（提示 uv add mootdx）。"""
        _reset_class_state()
        fake_module = types.ModuleType("mootdx")
        # fake_module 没有 Quotes，from-import 会失败
        with (
            patch.dict("sys.modules", {"mootdx": fake_module}),
            pytest.raises(RuntimeError, match="uv add mootdx"),
        ):
            provider._ensure_client()
        _reset_class_state()

    def test_ensure_client_connection_error(self, provider: MootDxProvider) -> None:
        """连接失败（ConnectionError）时应重置状态并重抛。"""
        _reset_class_state()
        with patch(
            "mootdx.quotes.Quotes.factory",
            side_effect=ConnectionError("network down"),
        ):
            with pytest.raises(ConnectionError):
                provider._ensure_client()
            assert MootDxProvider._client is None
            assert MootDxProvider._connected is False
        _reset_class_state()

    def test_ensure_client_general_exception(self, provider: MootDxProvider) -> None:
        """一般异常时也应重置状态并重抛。"""
        _reset_class_state()
        with patch(
            "mootdx.quotes.Quotes.factory",
            side_effect=OSError("some error"),
        ):
            with pytest.raises(OSError):
                provider._ensure_client()
            assert MootDxProvider._client is None
            assert MootDxProvider._connected is False
        _reset_class_state()


class TestEstimateBars:
    """_estimate_bars 静态方法测试。"""

    def test_daily_frequency(self) -> None:
        n = MootDxProvider._estimate_bars("2026-01-01", "2026-01-10", "d")
        assert n == int(9 * 1 * 1.2) + 5

    def test_5min_frequency(self) -> None:
        n = MootDxProvider._estimate_bars("2026-01-01", "2026-01-02", "5min")
        assert n == int(1 * 48 * 1.2) + 5

    def test_unknown_frequency_defaults_to_1(self) -> None:
        n = MootDxProvider._estimate_bars("2026-01-01", "2026-01-10", "unknown")
        assert n == int(9 * 1 * 1.2) + 5

    def test_invalid_date_falls_back_to_1_day(self) -> None:
        n = MootDxProvider._estimate_bars("not-a-date", "also-not-a-date", "d")
        assert n == int(1 * 1 * 1.2) + 5


class TestFetchBarsInRange:
    """_fetch_bars_in_range 方法测试。"""

    def _make_provider_with_client(self, mock_client: MagicMock) -> MootDxProvider:
        p = MootDxProvider()
        p._client = mock_client
        return p

    def test_fetch_bars_single_page(self) -> None:
        mock_client = MagicMock()
        df = pd.DataFrame(
            {"open": [100.0], "high": [101.0], "low": [99.0], "close": [100.0], "vol": [1e6], "amount": [1e8]},
            index=pd.DatetimeIndex([datetime(2026, 8, 1)]),
        )
        mock_client.bars.return_value = df
        p = self._make_provider_with_client(mock_client)
        result = p._fetch_bars_in_range("600519", 8, "2026-01-01", "2026-08-13")
        assert result is not None
        assert len(result) == 1

    def test_fetch_bars_empty_df(self) -> None:
        mock_client = MagicMock()
        mock_client.bars.return_value = pd.DataFrame()
        p = self._make_provider_with_client(mock_client)
        result = p._fetch_bars_in_range("600519", 8, "2026-01-01", "2026-08-13")
        assert result is None

    def test_fetch_bars_none_df(self) -> None:
        mock_client = MagicMock()
        mock_client.bars.return_value = None
        p = self._make_provider_with_client(mock_client)
        result = p._fetch_bars_in_range("600519", 8, "2026-01-01", "2026-08-13")
        assert result is None

    def test_fetch_bars_two_pages_date_covered_breaks(self) -> None:
        mock_client = MagicMock()
        # 第一页：800 行（不触发 len<800 断路），索引最新
        df1 = pd.DataFrame(
            {
                "open": [100.0] * 800,
                "high": [101.0] * 800,
                "low": [99.0] * 800,
                "close": [100.0] * 800,
                "vol": [1e6] * 800,
                "amount": [1e8] * 800,
            },
            index=pd.date_range("2026-08-01", periods=800, freq="D"),
        )
        # 第二页：1 行，索引很早（<= start_date），触发日期覆盖断路
        df2 = pd.DataFrame(
            {"open": [101.0], "high": [102.0], "low": [100.0], "close": [101.0], "vol": [1e6], "amount": [1e8]},
            index=pd.DatetimeIndex([datetime(2020, 1, 1)]),
        )
        mock_client.bars.side_effect = [df1, df2]
        p = self._make_provider_with_client(mock_client)
        result = p._fetch_bars_in_range("600519", 8, "2026-01-01", "2026-08-13")
        assert result is not None
        assert mock_client.bars.call_count == 2

    def test_fetch_bars_less_than_800_breaks(self) -> None:
        mock_client = MagicMock()
        df = pd.DataFrame(
            {"open": [100.0], "high": [101.0], "low": [99.0], "close": [100.0], "vol": [1e6], "amount": [1e8]},
            index=pd.DatetimeIndex([datetime(2026, 8, 1)]),
        )
        mock_client.bars.return_value = df
        p = self._make_provider_with_client(mock_client)
        p._fetch_bars_in_range("600519", 8, "2026-01-01", "2026-08-13")
        mock_client.bars.assert_called_once()


class TestFetchKline:
    """fetch_kline 方法测试。"""

    def test_fetch_kline_bj_returns_none(self, provider: MootDxProvider) -> None:
        """北交所标的 market < 0 时直接返回 None。"""
        _reset_class_state()
        with patch.object(MootDxProvider, "_ensure_client"):
            result = provider.fetch_kline("bj.920001", "2026-01-01", "2026-08-13")
            assert result is None
        _reset_class_state()

    def test_fetch_kline_success(self, provider: MootDxProvider) -> None:
        mock_df = pd.DataFrame(
            {
                "datetime": [datetime(2026, 8, 1), datetime(2026, 8, 4)],
                "open": [100.0, 102.0],
                "high": [103.0, 104.0],
                "low": [99.0, 101.0],
                "close": [101.0, 103.0],
                "vol": [1e6, 1.2e6],
                "amount": [1e8, 1.2e8],
            },
        )
        mock_client = MagicMock()
        mock_client.bars.return_value = mock_df
        _reset_class_state()
        with (
            patch.object(MootDxProvider, "_ensure_client"),
            patch.object(provider, "_client", mock_client),
        ):
            result = provider.fetch_kline("sh.600519", "2026-01-01", "2026-08-13")
            assert result is not None
            assert result.length == 2
        _reset_class_state()

    def test_fetch_kline_empty_result(self, provider: MootDxProvider) -> None:
        mock_client = MagicMock()
        mock_client.bars.return_value = None
        _reset_class_state()
        with (
            patch.object(MootDxProvider, "_ensure_client"),
            patch.object(provider, "_client", mock_client),
        ):
            result = provider.fetch_kline("sh.600519", "2026-01-01", "2026-08-13")
            assert result is None
        _reset_class_state()

    def test_fetch_kline_api_exception(self, provider: MootDxProvider) -> None:
        mock_client = MagicMock()
        mock_client.bars.side_effect = Exception("connection reset")
        _reset_class_state()
        with (
            patch.object(MootDxProvider, "_ensure_client"),
            patch.object(provider, "_client", mock_client),
        ):
            result = provider.fetch_kline("sh.600519", "2026-01-01", "2026-08-13")
            assert result is None
        _reset_class_state()

    def test_fetch_kline_date_filter_empty(self, provider: MootDxProvider) -> None:
        """mootdx 返回的数据都在请求区间外，过滤后为空应返回 None。"""
        mock_df = pd.DataFrame(
            {
                "datetime": [datetime(2026, 7, 1), datetime(2026, 7, 2)],
                "open": [100.0, 102.0],
                "high": [103.0, 104.0],
                "low": [99.0, 101.0],
                "close": [101.0, 103.0],
                "vol": [1e6, 1.2e6],
                "amount": [1e8, 1.2e8],
            },
        )
        mock_client = MagicMock()
        mock_client.bars.return_value = mock_df
        _reset_class_state()
        with (
            patch.object(MootDxProvider, "_ensure_client"),
            patch.object(provider, "_client", mock_client),
        ):
            result = provider.fetch_kline("sh.600519", "2026-08-01", "2026-08-13")
            assert result is None
        _reset_class_state()

    def test_fetch_kline_no_datetime_column(self, provider: MootDxProvider) -> None:
        """df 无 datetime 列时应通过 rename_axis 补齐。"""
        mock_df = pd.DataFrame(
            {
                "open": [100.0, 102.0],
                "high": [103.0, 104.0],
                "low": [99.0, 101.0],
                "close": [101.0, 103.0],
                "vol": [1e6, 1.2e6],
                "amount": [1e8, 1.2e8],
            },
            index=pd.DatetimeIndex([datetime(2026, 8, 1), datetime(2026, 8, 4)]),
        )
        mock_client = MagicMock()
        mock_client.bars.return_value = mock_df
        _reset_class_state()
        with (
            patch.object(MootDxProvider, "_ensure_client"),
            patch.object(provider, "_client", mock_client),
        ):
            result = provider.fetch_kline("sh.600519", "2026-01-01", "2026-08-13")
            assert result is not None
            assert result.length == 2
        _reset_class_state()


class TestFetchQuote:
    """fetch_quote 方法测试。"""

    def test_fetch_quote_success(self, provider: MootDxProvider) -> None:
        mock_client = MagicMock()
        mock_client.quotes.return_value = [{"price": 1800.5}]
        _reset_class_state()
        with (
            patch.object(MootDxProvider, "_ensure_client"),
            patch.object(provider, "_client", mock_client),
        ):
            result = provider.fetch_quote("sh.600519")
            assert result is not None
            assert result.ticker == "sh.600519"
            assert result.price == 1800.5
            assert result.source == "mootdx"
        _reset_class_state()

    def test_fetch_quote_empty(self, provider: MootDxProvider) -> None:
        mock_client = MagicMock()
        mock_client.quotes.return_value = []
        _reset_class_state()
        with (
            patch.object(MootDxProvider, "_ensure_client"),
            patch.object(provider, "_client", mock_client),
        ):
            result = provider.fetch_quote("sh.600519")
            assert result is None
        _reset_class_state()

    def test_fetch_quote_exception(self, provider: MootDxProvider) -> None:
        mock_client = MagicMock()
        mock_client.quotes.side_effect = Exception("rate limited")
        _reset_class_state()
        with (
            patch.object(MootDxProvider, "_ensure_client"),
            patch.object(provider, "_client", mock_client),
        ):
            result = provider.fetch_quote("sh.600519")
            assert result is None
        _reset_class_state()


class TestFetchMetadata:
    """fetch_metadata 方法测试。"""

    def test_fetch_metadata_returns_none(self, provider: MootDxProvider) -> None:
        assert provider.fetch_metadata("sh.600519") is None


class TestHealthCheck:
    """health_check 方法测试。"""

    def test_health_check_success(self, provider: MootDxProvider) -> None:
        mock_df = pd.DataFrame(
            {
                "datetime": [datetime(2026, 8, 1)],
                "open": [100.0],
                "high": [101.0],
                "low": [99.0],
                "close": [100.5],
                "vol": [1e6],
                "amount": [1e8],
            },
        )
        mock_client = MagicMock()
        mock_client.bars.return_value = mock_df
        _reset_class_state()
        with (
            patch.object(MootDxProvider, "_ensure_client"),
            patch.object(provider, "_client", mock_client),
        ):
            assert provider.health_check() is True
        _reset_class_state()

    def test_health_check_empty_result(self, provider: MootDxProvider) -> None:
        mock_client = MagicMock()
        mock_client.bars.return_value = pd.DataFrame()
        _reset_class_state()
        with (
            patch.object(MootDxProvider, "_ensure_client"),
            patch.object(provider, "_client", mock_client),
        ):
            assert provider.health_check() is False
        _reset_class_state()

    def test_health_check_exception(self, provider: MootDxProvider) -> None:
        mock_client = MagicMock()
        mock_client.bars.side_effect = Exception("fail")
        _reset_class_state()
        with (
            patch.object(MootDxProvider, "_ensure_client"),
            patch.object(provider, "_client", mock_client),
        ):
            assert provider.health_check() is False
        _reset_class_state()
