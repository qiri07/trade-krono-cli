"""测试 AkShareProvider — 开源 A 股数据源（需要 akshare 包）。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

akshare_available = pytest.importorskip("akshare", reason="akshare not installed")


class TestAkShareProvider:
    @pytest.fixture
    def provider(self):
        from trade_krono_cli.data_providers.akshare_provider import AkShareProvider

        return AkShareProvider()

    def test_name(self, provider) -> None:
        assert provider.name == "akshare"
        assert provider.supports_kline is True
        assert provider.supports_quote is True
        assert provider.supports_metadata is False

    def test_ticker_conversion(self, provider) -> None:
        assert provider._ticker_to_ak("sh.600519") == "600519"
        assert provider._ticker_to_ak("sz.000001") == "000001"
        assert provider._ak_to_ticker("600519") == "sh.600519"
        assert provider._ak_to_ticker("000001") == "sz.000001"

    def test_ensure_import_success(self, provider) -> None:
        """_ensure_import 成功时应设置 _ak 属性。"""
        mock_ak = MagicMock()
        # 直接重置实例属性，不使用 patch.object
        provider.__class__._ak = None
        with patch.dict("sys.modules", {"akshare": mock_ak}):
            provider._ensure_import()
            assert provider._ak is not None
        # 恢复
        provider.__class__._ak = None

    def test_ensure_import_already_loaded(self, provider) -> None:
        """_ak 已加载时 _ensure_import 不应重复导入。"""
        mock_ak = MagicMock()
        provider.__class__._ak = mock_ak
        with patch("builtins.__import__") as mock_import:
            provider._ensure_import()
            mock_import.assert_not_called()
        # 恢复
        provider.__class__._ak = None

    def test_fetch_kline_success(self, provider) -> None:
        """fetch_kline 成功时应返回 KlineData。"""
        mock_df = pd.DataFrame(
            {
                "日期": ["2026-08-01", "2026-08-04"],
                "开盘": [100.0, 102.0],
                "最高": [103.0, 104.0],
                "最低": [99.0, 101.0],
                "收盘": [101.0, 103.0],
                "成交量": [1e6, 1.2e6],
                "成交额": [1e8, 1.2e8],
            },
        )
        from trade_krono_cli.data_providers.akshare_provider import AkShareProvider

        with patch.object(AkShareProvider, "_ensure_import"):
            with patch.object(provider, "_ak", create=True) as mock_ak:
                mock_ak.stock_zh_a_hist.return_value = mock_df
                result = provider.fetch_kline("sh.600519", "2026-01-01", "2026-08-13")
                assert result is not None
                assert result.length == 2
                assert result.open[0] == 100.0
                assert result.close[-1] == 103.0

    def test_fetch_kline_unsupported_frequency(self, provider) -> None:
        """fetch_kline 不支持非日频时应返回 None。"""
        result = provider.fetch_kline("sh.600519", "2026-01-01", "2026-08-13", frequency="w")
        assert result is None

    def test_fetch_kline_empty_result(self, provider) -> None:
        """fetch_kline 返回空 DataFrame 时应返回 None。"""
        from trade_krono_cli.data_providers.akshare_provider import AkShareProvider

        with patch.object(AkShareProvider, "_ensure_import"):
            with patch.object(provider, "_ak", create=True) as mock_ak:
                mock_ak.stock_zh_a_hist.return_value = pd.DataFrame()
                result = provider.fetch_kline("sh.600519", "2026-01-01", "2026-08-13")
                assert result is None

    def test_fetch_kline_api_exception(self, provider) -> None:
        """fetch_kline API 异常时应返回 None 而非抛出。"""
        from trade_krono_cli.data_providers.akshare_provider import AkShareProvider

        with patch.object(AkShareProvider, "_ensure_import"):
            with patch.object(provider, "_ak", create=True) as mock_ak:
                mock_ak.stock_zh_a_hist.side_effect = Exception("API error")
                result = provider.fetch_kline("sh.600519", "2026-01-01", "2026-08-13")
                assert result is None

    def test_fetch_kline_none_df(self, provider) -> None:
        """fetch_kline API 返回 None 时应返回 None。"""
        from trade_krono_cli.data_providers.akshare_provider import AkShareProvider

        with patch.object(AkShareProvider, "_ensure_import"):
            with patch.object(provider, "_ak", create=True) as mock_ak:
                mock_ak.stock_zh_a_hist.return_value = None
                result = provider.fetch_kline("sh.600519", "2026-01-01", "2026-08-13")
                assert result is None

    def test_fetch_quote_success(self, provider) -> None:
        """fetch_quote 成功时应返回 RealtimeQuote。"""
        mock_df = pd.DataFrame(
            {
                "代码": ["600519"],
                "最新价": [1800.0],
                "市盈率-动态": [35.5],
                "市净率": [9.2],
                "总市值": [2.26e12],
                "换手率": [0.5],
            },
        )
        from trade_krono_cli.data_providers.akshare_provider import AkShareProvider

        with patch.object(AkShareProvider, "_ensure_import"):
            with patch.object(AkShareProvider, "_get_full_market_cache", return_value=mock_df):
                result = provider.fetch_quote("sh.600519")
                assert result is not None
                assert result.price == 1800.0
                assert result.pe == 35.5
                assert result.pb == 9.2
                assert result.source == "akshare"

    def test_fetch_quote_ticker_not_found(self, provider) -> None:
        """fetch_quote 股票不在全市场行情中时应返回 None。"""
        mock_df = pd.DataFrame({"代码": ["000001"], "最新价": [10.0]})
        from trade_krono_cli.data_providers.akshare_provider import AkShareProvider

        with patch.object(AkShareProvider, "_ensure_import"):
            with patch.object(AkShareProvider, "_get_full_market_cache", return_value=mock_df):
                result = provider.fetch_quote("sh.600519")
                assert result is None

    def test_fetch_quote_cache_return_none(self, provider) -> None:
        """缓存返回 None 时 fetch_quote 应返回 None。"""
        from trade_krono_cli.data_providers.akshare_provider import AkShareProvider

        with patch.object(AkShareProvider, "_ensure_import"):
            with patch.object(AkShareProvider, "_get_full_market_cache", return_value=None):
                result = provider.fetch_quote("sh.600519")
                assert result is None

    def test_fetch_quote_api_exception(self, provider) -> None:
        """fetch_quote API 异常时应返回 None。"""
        from trade_krono_cli.data_providers.akshare_provider import AkShareProvider

        with patch.object(AkShareProvider, "_ensure_import"):
            with patch.object(
                AkShareProvider, "_get_full_market_cache", side_effect=Exception("fail")
            ):
                result = provider.fetch_quote("sh.600519")
                assert result is None

    def test_get_full_market_cache_hit(self, provider) -> None:
        """缓存命中时直接返回缓存数据。"""
        import trade_krono_cli.data_providers.akshare_provider as akmod

        mock_df = pd.DataFrame({"code": ["600519"]})
        with patch.object(provider.__class__, "_ak", create=True):
            with patch.object(
                akmod, "_full_market_cache", {"data": mock_df, "timestamp": 9999999999.0}
            ):
                result = provider._get_full_market_cache()
                assert result is mock_df

    def test_get_full_market_cache_miss(self, provider) -> None:
        """缓存过期时重新拉取并更新缓存。"""
        import trade_krono_cli.data_providers.akshare_provider as akmod

        mock_df = pd.DataFrame({"代码": ["600519"], "最新价": [1800.0]})
        with patch.object(akmod.AkShareProvider, "_ak", create=True) as mock_ak:
            mock_ak.stock_zh_a_spot_em.return_value = mock_df
            with patch.object(akmod, "_full_market_cache", {"data": None, "timestamp": 0.0}):
                result = akmod.AkShareProvider._get_full_market_cache()
                assert result is not None
                # 验证缓存被更新
                assert akmod._full_market_cache["data"] is not None

    def test_get_full_market_cache_exception(self, provider) -> None:
        """缓存拉取失败时返回 None。"""
        import trade_krono_cli.data_providers.akshare_provider as akmod

        with patch.object(akmod.AkShareProvider, "_ak", create=True) as mock_ak:
            mock_ak.stock_zh_a_spot_em.side_effect = Exception("network error")
            with patch.object(akmod, "_full_market_cache", {"data": None, "timestamp": 0.0}):
                result = akmod.AkShareProvider._get_full_market_cache()
                assert result is None

    def test_ticker_conversion_sz_codes(self, provider) -> None:
        """测试 SZ 板块的代码转换。"""
        assert provider._ak_to_ticker("000001") == "sz.000001"
        assert provider._ak_to_ticker("300001") == "sz.300001"
        assert provider._ak_to_ticker("002001") == "sz.002001"

    def test_ticker_conversion_sh_codes(self, provider) -> None:
        """测试 SH 板块的代码转换。"""
        assert provider._ak_to_ticker("600519") == "sh.600519"
        assert provider._ak_to_ticker("510050") == "sh.510050"
        assert provider._ak_to_ticker("999999") == "sh.999999"

    def test_fetch_metadata_returns_none(self, provider) -> None:
        assert provider.fetch_metadata("sh.600519") is None

    def test_health_check_success(self, provider) -> None:
        import pandas as pd

        mock_df = pd.DataFrame(
            {
                "日期": ["2026-08-01"],
                "开盘": [100],
                "最高": [102],
                "最低": [99],
                "收盘": [101],
                "成交量": [1e6],
                "成交额": [1e8],
            },
        )
        from trade_krono_cli.data_providers.akshare_provider import AkShareProvider

        with patch.object(AkShareProvider, "_ensure_import"):
            with patch.object(provider, "_ak", create=True) as mock_ak:
                mock_ak.stock_zh_a_hist.return_value = mock_df
                assert provider.health_check() is True

    def test_health_check_failure(self, provider) -> None:
        from trade_krono_cli.data_providers.akshare_provider import AkShareProvider

        with patch.object(AkShareProvider, "_ensure_import", side_effect=Exception("fail")):
            assert provider.health_check() is False

    def test_health_check_empty_df(self, provider) -> None:
        """health_check 返回空 DataFrame 时应返回 False。"""
        from trade_krono_cli.data_providers.akshare_provider import AkShareProvider

        with patch.object(AkShareProvider, "_ensure_import"):
            with patch.object(provider, "_ak", create=True) as mock_ak:
                mock_ak.stock_zh_a_hist.return_value = pd.DataFrame()
                assert provider.health_check() is False


# ═══════════════════════════════════════════════════════
# MootDxProvider 测试（需要 mootdx 包）
# ═══════════════════════════════════════════════════════

mootdx_available = pytest.importorskip("mootdx", reason="mootdx not installed")
