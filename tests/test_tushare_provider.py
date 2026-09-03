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


# ═══════════════════════════════════════════════════════
# DataProviderFactory 测试
# ═══════════════════════════════════════════════════════
