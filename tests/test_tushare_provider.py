"""测试 TushareProvider — Tushare 金融数据源（需要 tushare 包）。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

tushare_available = pytest.importorskip("tushare", reason="tushare not installed")


class TestTushareProvider:
    @pytest.fixture
    def provider(self):
        from trade_krono_cli.data_providers.tushare_provider import TushareProvider

        return TushareProvider()

    def test_name(self, provider) -> None:
        assert provider.name == "tushare"
        assert provider.supports_kline is True
        assert provider.supports_quote is True
        assert provider.supports_metadata is True

    def test_ensure_import_success(self, provider) -> None:
        """_ensure_import 成功时应设置 _ts 属性。"""
        mock_ts = MagicMock()
        # 直接重置类属性
        provider.__class__._ts = None
        provider.__class__._token = ""
        with patch.dict("os.environ", {"TUSHARE_TOKEN": "fake_token"}):
            with patch.dict("sys.modules", {"tushare": mock_ts}):
                provider._ensure_import()
                assert provider._ts is not None
                assert provider._token == "fake_token"
        # 恢复
        provider.__class__._ts = None
        provider.__class__._token = ""

    def test_ensure_import_already_loaded(self, provider) -> None:
        """_ts 已加载时 _ensure_import 不应重复导入。"""
        mock_ts = MagicMock()
        provider.__class__._ts = mock_ts
        with patch.dict("os.environ", {"TUSHARE_TOKEN": "fake_token"}):
            with patch("builtins.__import__") as mock_import:
                provider._ensure_import()
                mock_import.assert_not_called()
        # 恢复
        provider.__class__._ts = None

    def test_ensure_import_missing_token(self) -> None:
        """未设置 TUSHARE_TOKEN 时应抛出 RuntimeError。"""
        from trade_krono_cli.data_providers.tushare_provider import TushareProvider

        with patch.dict("os.environ", {}, clear=True):
            provider = TushareProvider()
            provider.__class__._ts = None
            provider.__class__._token = ""
            with pytest.raises(RuntimeError, match="TUSHARE_TOKEN"):
                provider._ensure_import()
            # 恢复
            provider.__class__._ts = None
            provider.__class__._token = ""

    def test_ensure_import_import_error(self) -> None:
        """tushare 包未安装时应抛出 RuntimeError。"""
        from trade_krono_cli.data_providers.tushare_provider import TushareProvider

        with patch.dict("os.environ", {"TUSHARE_TOKEN": "fake_token"}):
            with patch.dict("sys.modules", {"tushare": None}):
                provider = TushareProvider()
                provider._ts = None
                provider._token = ""
                with patch.object(provider.__class__, "_ts", None):
                    with patch.object(provider.__class__, "_token", ""):
                        with pytest.raises(RuntimeError, match="tushare 未安装"):
                            provider._ensure_import()

    def test_fetch_kline_success(self, provider) -> None:
        mock_df = pd.DataFrame(
            {
                "trade_date": ["20260801", "20260804"],
                "open": [100.0, 102.0],
                "high": [103.0, 104.0],
                "low": [99.0, 101.0],
                "close": [101.0, 103.0],
                "vol": [1e6, 1.2e6],
                "amount": [1e8, 1.2e8],
            },
        )
        mock_ts = MagicMock()
        mock_ts.pro_bar.return_value = mock_df

        with patch.dict("os.environ", {"TUSHARE_TOKEN": "fake_token"}):
            with patch.object(provider.__class__, "_ts", mock_ts):
                result = provider.fetch_kline("sh.600519", "2026-01-01", "2026-08-13")
                assert result is not None
                assert result.length == 2
                assert result.open[0] == 100.0
                assert result.close[-1] == 103.0

    def test_fetch_kline_empty_result(self, provider) -> None:
        """fetch_kline 返回空 DataFrame 时应返回 None。"""
        mock_ts = MagicMock()
        mock_ts.pro_bar.return_value = pd.DataFrame()

        with patch.dict("os.environ", {"TUSHARE_TOKEN": "fake_token"}):
            with patch.object(provider.__class__, "_ts", mock_ts):
                result = provider.fetch_kline("sh.600519", "2026-01-01", "2026-08-13")
                assert result is None

    def test_fetch_kline_none_df(self, provider) -> None:
        """fetch_kline API 返回 None 时应返回 None。"""
        mock_ts = MagicMock()
        mock_ts.pro_bar.return_value = None

        with patch.dict("os.environ", {"TUSHARE_TOKEN": "fake_token"}):
            with patch.object(provider.__class__, "_ts", mock_ts):
                result = provider.fetch_kline("sh.600519", "2026-01-01", "2026-08-13")
                assert result is None

    def test_fetch_kline_api_exception(self, provider) -> None:
        """fetch_kline API 异常时应返回 None 而非抛出。"""
        mock_ts = MagicMock()
        mock_ts.pro_bar.side_effect = Exception("API rate limit")

        with patch.dict("os.environ", {"TUSHARE_TOKEN": "fake_token"}):
            with patch.object(provider.__class__, "_ts", mock_ts):
                result = provider.fetch_kline("sh.600519", "2026-01-01", "2026-08-13")
                assert result is None

    def test_fetch_kline_sz_ticker(self, provider) -> None:
        """SZ 股票代码转换正确。"""
        mock_df = pd.DataFrame(
            {
                "trade_date": ["20260801"],
                "open": [10.0],
                "high": [10.5],
                "low": [9.8],
                "close": [10.2],
                "vol": [5e5],
                "amount": [5e6],
            },
        )
        mock_ts = MagicMock()
        mock_ts.pro_bar.return_value = mock_df

        with patch.dict("os.environ", {"TUSHARE_TOKEN": "fake_token"}):
            with patch.object(provider.__class__, "_ts", mock_ts):
                result = provider.fetch_kline("sz.000001", "2026-01-01", "2026-08-13")
                assert result is not None
                # 验证调用了 pro_bar
                mock_ts.pro_bar.assert_called_once()

    def test_fetch_metadata_success(self, provider) -> None:
        mock_df = pd.DataFrame(
            {
                "ts_code": ["600519.SH"],
                "name": ["贵州茅台"],
                "industry": ["白酒"],
                "list_date": ["1999-11-10"],
                "delist_date": [None],
            },
        )
        mock_ts = MagicMock()
        mock_ts.stock_basic.return_value = mock_df

        with patch.dict("os.environ", {"TUSHARE_TOKEN": "fake_token"}):
            with patch.object(provider.__class__, "_ts", mock_ts):
                meta = provider.fetch_metadata("sh.600519")
                assert meta is not None
                assert meta.industry == "白酒"
                assert meta.ipo_date == "1999-11-10"
                assert meta.is_st is False

    def test_fetch_metadata_st_stock(self, provider) -> None:
        mock_df = pd.DataFrame(
            {
                "ts_code": ["601234.SH"],
                "name": ["*ST某某"],
                "industry": ["机械"],
                "list_date": ["20200101"],
                "delist_date": [None],
            },
        )
        mock_ts = MagicMock()
        mock_ts.stock_basic.return_value = mock_df

        with patch.dict("os.environ", {"TUSHARE_TOKEN": "fake_token"}):
            with patch.object(provider.__class__, "_ts", mock_ts):
                meta = provider.fetch_metadata("sh.601234")
                assert meta is not None
                assert meta.is_st is True

    def test_fetch_metadata_empty_result(self, provider) -> None:
        """fetch_metadata 返回空 DataFrame 时应返回 None。"""
        mock_ts = MagicMock()
        mock_ts.stock_basic.return_value = pd.DataFrame()

        with patch.dict("os.environ", {"TUSHARE_TOKEN": "fake_token"}):
            with patch.object(provider.__class__, "_ts", mock_ts):
                result = provider.fetch_metadata("sh.600519")
                assert result is None

    def test_fetch_metadata_api_exception(self, provider) -> None:
        """fetch_metadata API 异常时应返回 None。"""
        mock_ts = MagicMock()
        mock_ts.stock_basic.side_effect = Exception("API error")

        with patch.dict("os.environ", {"TUSHARE_TOKEN": "fake_token"}):
            with patch.object(provider.__class__, "_ts", mock_ts):
                result = provider.fetch_metadata("sh.600519")
                assert result is None

    def test_fetch_quote_success(self, provider) -> None:
        """fetch_quote 成功时应返回 RealtimeQuote。"""
        mock_df = pd.DataFrame(
            {
                "last_close": [1750.0],
                "price": [1800.0],
                "pe": [35.5],
                "pb": [9.2],
                "total_mv": [2.26e12],
            },
        )
        mock_ts = MagicMock()
        mock_ts.realtime_quote.return_value = mock_df

        with patch.dict("os.environ", {"TUSHARE_TOKEN": "fake_token"}):
            with patch.object(provider.__class__, "_ts", mock_ts):
                result = provider.fetch_quote("sh.600519")
                assert result is not None
                # price 优先取 last_close（前收盘价）
                assert result.price == 1750.0
                assert result.pe == 35.5
                assert result.pb == 9.2
                assert result.source == "tushare"

    def test_fetch_quote_empty_result(self, provider) -> None:
        """fetch_quote 返回空 DataFrame 时应返回 None。"""
        mock_ts = MagicMock()
        mock_ts.realtime_quote.return_value = pd.DataFrame()

        with patch.dict("os.environ", {"TUSHARE_TOKEN": "fake_token"}):
            with patch.object(provider.__class__, "_ts", mock_ts):
                result = provider.fetch_quote("sh.600519")
                assert result is None

    def test_fetch_quote_api_exception(self, provider) -> None:
        """fetch_quote API 异常时应返回 None。"""
        mock_ts = MagicMock()
        mock_ts.realtime_quote.side_effect = Exception("API error")

        with patch.dict("os.environ", {"TUSHARE_TOKEN": "fake_token"}):
            with patch.object(provider.__class__, "_ts", mock_ts):
                result = provider.fetch_quote("sh.600519")
                assert result is None

    def test_health_check_success(self, provider) -> None:
        mock_df = pd.DataFrame({"ts_code": ["600519.SH"]})
        mock_ts = MagicMock()
        mock_ts.stock_basic.return_value = mock_df

        with patch.dict("os.environ", {"TUSHARE_TOKEN": "fake_token"}):
            with patch.object(provider.__class__, "_ts", mock_ts):
                assert provider.health_check() is True

    def test_health_check_failure(self, provider) -> None:
        mock_ts = MagicMock()
        mock_ts.stock_basic.side_effect = Exception("fail")

        with patch.dict("os.environ", {"TUSHARE_TOKEN": "fake_token"}):
            with patch.object(provider.__class__, "_ts", mock_ts):
                assert provider.health_check() is False

    def test_health_check_empty_result(self, provider) -> None:
        """health_check 返回空结果时应返回 False。"""
        mock_ts = MagicMock()
        mock_ts.stock_basic.return_value = pd.DataFrame()

        with patch.dict("os.environ", {"TUSHARE_TOKEN": "fake_token"}):
            with patch.object(provider.__class__, "_ts", mock_ts):
                assert provider.health_check() is False


# ═══════════════════════════════════════════════════════
# DataProviderFactory 测试
# ═══════════════════════════════════════════════════════
