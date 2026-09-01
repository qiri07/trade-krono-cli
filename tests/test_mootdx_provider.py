"""测试 MootDxProvider — 同花顺 Moomoo 数据源（需要 mootdx 包）。"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

mootdx_available = pytest.importorskip("mootdx", reason="mootdx not installed")


class TestMootDxProvider:
    @pytest.fixture
    def provider(self):
        from trade_krono_cli.data_providers.mootdx_provider import MootDxProvider

        return MootDxProvider()

    def test_name(self, provider):
        assert provider.name == "mootdx"
        assert provider.supports_kline is True
        assert provider.supports_quote is True
        assert provider.supports_metadata is False

    def test_ticker_conversion(self, provider):
        market, code = provider._ticker_to_mootdx("sh.600519")
        assert market == 1
        assert code == "600519"
        market, code = provider._ticker_to_mootdx("sz.000001")
        assert market == 0
        assert code == "000001"

    def test_fetch_kline_success(self, provider):

        mock_df = pd.DataFrame(
            {
                "datetime": [datetime(2026, 8, 1), datetime(2026, 8, 4)],
                "open": [100.0, 102.0],
                "high": [103.0, 104.0],
                "low": [99.0, 101.0],
                "close": [101.0, 103.0],
                "vol": [1e6, 1.2e6],
                "amount": [1e8, 1.2e8],
            }
        )
        mock_client = MagicMock()
        mock_client.bars.return_value = mock_df

        from trade_krono_cli.data_providers.mootdx_provider import MootDxProvider

        with patch.object(MootDxProvider, "_ensure_client"):
            with patch.object(provider, "_client", mock_client):
                result = provider.fetch_kline("sh.600519", "2026-01-01", "2026-08-13")
                assert result is not None
                assert result.length == 2

    def test_fetch_kline_empty(self, provider):
        mock_client = MagicMock()
        mock_client.bars.return_value = None

        from trade_krono_cli.data_providers.mootdx_provider import MootDxProvider

        with patch.object(MootDxProvider, "_ensure_client"):
            with patch.object(provider, "_client", mock_client):
                result = provider.fetch_kline("sh.600519", "2026-01-01", "2026-08-13")
                assert result is None

    def test_fetch_metadata_returns_none(self, provider):
        assert provider.fetch_metadata("sh.600519") is None

    def test_health_check_success(self, provider):

        mock_df = pd.DataFrame(
            {
                "datetime": [datetime(2026, 8, 1)],
                "open": [100.0],
                "high": [101.0],
                "low": [99.0],
                "close": [100.5],
                "vol": [1e6],
                "amount": [1e8],
            }
        )
        mock_client = MagicMock()
        mock_client.bars.return_value = mock_df

        from trade_krono_cli.data_providers.mootdx_provider import MootDxProvider

        with patch.object(MootDxProvider, "_ensure_client"):
            with patch.object(provider, "_client", mock_client):
                assert provider.health_check() is True

    def test_health_check_failure(self, provider):
        mock_client = MagicMock()
        mock_client.bars.side_effect = Exception("fail")

        from trade_krono_cli.data_providers.mootdx_provider import MootDxProvider

        with patch.object(MootDxProvider, "_ensure_client"):
            with patch.object(provider, "_client", mock_client):
                assert provider.health_check() is False


# ═══════════════════════════════════════════════════════
# TushareProvider 测试（需要 tushare 包）
# ═══════════════════════════════════════════════════════

tushare_available = pytest.importorskip("tushare", reason="tushare not installed")
