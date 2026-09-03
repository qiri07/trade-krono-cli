"""测试 AkShareProvider — 开源 A 股数据源（需要 akshare 包）。"""

from __future__ import annotations

from unittest.mock import patch

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

    def test_fetch_kline_import_error(self, provider) -> None:
        with patch.object(
            __import__(
                "trade_krono_cli.data_providers.akshare_provider", fromlist=["AkShareProvider"],
            ).AkShareProvider,
            "_ensure_import",
            side_effect=RuntimeError("not installed"),
        ):
            result = provider.fetch_kline("sh.600519", "2026-01-01", "2026-08-13")
            assert result is None

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


# ═══════════════════════════════════════════════════════
# MootDxProvider 测试（需要 mootdx 包）
# ═══════════════════════════════════════════════════════

mootdx_available = pytest.importorskip("mootdx", reason="mootdx not installed")
