"""测试 BaostockProvider — 国内权威 A 股数据源。"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest


class TestBaostockProvider:
    @pytest.fixture
    def provider(self):
        from trade_krono_cli.data_providers.baostock_provider import BaostockProvider

        return BaostockProvider()

    def test_name(self, provider):
        assert provider.name == "baostock"
        assert provider.supports_kline is True
        assert provider.supports_quote is False
        assert provider.supports_metadata is True

    def test_fetch_kline_import_error(self, provider):
        with patch(
            "trade_krono_cli.data_providers.baostock_provider.BaostockProvider._ensure_import",
            side_effect=RuntimeError("baostock not installed"),
        ):
            with pytest.raises(RuntimeError, match="baostock not installed"):
                provider.fetch_kline("sh.600519", "2026-01-01", "2026-08-13")

    def test_fetch_kline_success(self, provider):
        mock_rs = MagicMock()
        mock_rs.error_code = "0"
        mock_rs.fields = ["date", "open", "high", "low", "close", "volume", "amount"]
        mock_rs.get_row_data.side_effect = [
            ("2026-08-01", 100, 103, 99, 101, 1000000, 100000000),
            ("2026-08-04", 102, 104, 101, 103, 1200000, 120000000),
        ]
        mock_rs.next.side_effect = [True, True, False]

        with (
            patch(
                "trade_krono_cli.data_providers.baostock_provider.BaostockProvider._ensure_import"
            ),
            patch("trade_krono_cli.data_providers.baostock_provider.BaostockProvider._get_limiter"),
            patch(
                "trade_krono_cli.data_providers.baostock_provider.BaostockProvider._ensure_login"
            ),
            patch("trade_krono_cli.data_providers.baostock_provider._bs") as mock_bs,
        ):
            mock_bs.query_history_k_data_plus.return_value = mock_rs
            result = provider.fetch_kline("sh.600519", "2026-01-01", "2026-08-13")
            assert result is not None
            assert result.length == 2
            assert result.close[1] == 103.0

    def test_fetch_kline_error_code(self, provider):
        mock_rs = MagicMock()
        mock_rs.error_code = "runerror"
        mock_rs.error_msg = "query failed"

        with (
            patch(
                "trade_krono_cli.data_providers.baostock_provider.BaostockProvider._ensure_import"
            ),
            patch("trade_krono_cli.data_providers.baostock_provider.BaostockProvider._get_limiter"),
            patch(
                "trade_krono_cli.data_providers.baostock_provider.BaostockProvider._ensure_login"
            ),
            patch("trade_krono_cli.data_providers.baostock_provider._bs") as mock_bs,
        ):
            mock_bs.query_history_k_data_plus.return_value = mock_rs
            result = provider.fetch_kline("sh.600519", "2026-01-01", "2026-08-13")
            assert result is None

    def test_fetch_quote_returns_none(self, provider):
        assert provider.fetch_quote("sh.600519") is None

    def test_fetch_metadata_success(self, provider):
        mock_rs = MagicMock()
        mock_rs.error_code = "0"
        mock_rs.get_row_data.return_value = ("sh.600519", "贵州茅台", "1999-11-10", None)
        mock_rs.next.side_effect = [True, False]

        with (
            patch(
                "trade_krono_cli.data_providers.baostock_provider.BaostockProvider._ensure_import"
            ),
            patch(
                "trade_krono_cli.data_providers.baostock_provider.BaostockProvider._ensure_login"
            ),
            patch("trade_krono_cli.data_providers.baostock_provider._bs") as mock_bs,
        ):
            mock_bs.query_stock_basic.return_value = mock_rs
            meta = provider.fetch_metadata("sh.600519")
            assert meta is not None
            assert meta.ticker == "sh.600519"
            assert meta.ipo_date == "1999-11-10"
            assert meta.is_st is False

    def test_fetch_metadata_st_stock(self, provider):
        mock_rs = MagicMock()
        mock_rs.error_code = "0"
        mock_rs.get_row_data.return_value = ("sh.601234", "*ST某某", "2020-01-01", None)
        mock_rs.next.side_effect = [True, False]

        with (
            patch(
                "trade_krono_cli.data_providers.baostock_provider.BaostockProvider._ensure_import"
            ),
            patch(
                "trade_krono_cli.data_providers.baostock_provider.BaostockProvider._ensure_login"
            ),
            patch("trade_krono_cli.data_providers.baostock_provider._bs") as mock_bs,
        ):
            mock_bs.query_stock_basic.return_value = mock_rs
            meta = provider.fetch_metadata("sh.601234")
            assert meta is not None
            assert meta.is_st is True

    def test_check_st_status_cached(self, provider):
        mock_rs = MagicMock()
        mock_rs.error_code = "0"
        mock_rs.get_row_data.return_value = ("sh.601234", "ST某某", "2020-01-01", None)
        mock_rs.next.side_effect = [True, False]

        with (
            patch(
                "trade_krono_cli.data_providers.baostock_provider.BaostockProvider._ensure_import"
            ),
            patch(
                "trade_krono_cli.data_providers.baostock_provider.BaostockProvider._ensure_login"
            ),
            patch("trade_krono_cli.data_providers.baostock_provider._bs") as mock_bs,
        ):
            mock_bs.query_stock_basic.return_value = mock_rs
            assert provider.check_st_status("sh.601234") is True
            # 第二次调用应命中缓存，不调用 API
            with patch.object(mock_bs, "query_stock_basic") as mock_q:
                assert provider.check_st_status("sh.601234") is True
                mock_q.assert_not_called()

    def test_check_delisted_future(self, provider):
        future_date = (datetime.now() + __import__("datetime").timedelta(days=365)).strftime(
            "%Y-%m-%d"
        )
        mock_rs = MagicMock()
        mock_rs.error_code = "0"
        mock_rs.get_row_data.return_value = ("sh.600001", "某股", "2020-01-01", future_date)
        mock_rs.next.side_effect = [True, False]

        with (
            patch(
                "trade_krono_cli.data_providers.baostock_provider.BaostockProvider._ensure_import"
            ),
            patch(
                "trade_krono_cli.data_providers.baostock_provider.BaostockProvider._ensure_login"
            ),
            patch("trade_krono_cli.data_providers.baostock_provider._bs") as mock_bs,
        ):
            mock_bs.query_stock_basic.return_value = mock_rs
            assert provider.check_delisted("sh.600001") is False

    def test_check_delisted_past(self, provider):
        past_date = "2020-01-01"
        mock_rs = MagicMock()
        mock_rs.error_code = "0"
        mock_rs.get_row_data.return_value = ("sh.600001", "某股", "2010-01-01", past_date)
        mock_rs.next.side_effect = [True, False]

        with (
            patch(
                "trade_krono_cli.data_providers.baostock_provider.BaostockProvider._ensure_import"
            ),
            patch(
                "trade_krono_cli.data_providers.baostock_provider.BaostockProvider._ensure_login"
            ),
            patch("trade_krono_cli.data_providers.baostock_provider._bs") as mock_bs,
        ):
            mock_bs.query_stock_basic.return_value = mock_rs
            assert provider.check_delisted("sh.600001") is True

    def test_check_new_stock_yes(self, provider):
        recent_ipo = (datetime.now() - __import__("datetime").timedelta(days=30)).strftime(
            "%Y-%m-%d"
        )
        mock_rs = MagicMock()
        mock_rs.error_code = "0"
        mock_rs.get_row_data.return_value = ("sh.600001", "某股", recent_ipo, None)
        mock_rs.next.side_effect = [True, False]

        today = datetime.now().strftime("%Y-%m-%d")
        with (
            patch(
                "trade_krono_cli.data_providers.baostock_provider.BaostockProvider._ensure_import"
            ),
            patch(
                "trade_krono_cli.data_providers.baostock_provider.BaostockProvider._ensure_login"
            ),
            patch("trade_krono_cli.data_providers.baostock_provider._bs") as mock_bs,
        ):
            mock_bs.query_stock_basic.return_value = mock_rs
            is_new, reason = provider.check_new_stock("sh.600001", today, min_listing_days=60)
            assert is_new is True

    def test_check_new_stock_no(self, provider):
        old_ipo = "2010-01-01"
        mock_rs = MagicMock()
        mock_rs.error_code = "0"
        mock_rs.get_row_data.return_value = ("sh.600001", "某股", old_ipo, None)
        mock_rs.next.side_effect = [True, False]

        today = datetime.now().strftime("%Y-%m-%d")
        with (
            patch(
                "trade_krono_cli.data_providers.baostock_provider.BaostockProvider._ensure_import"
            ),
            patch(
                "trade_krono_cli.data_providers.baostock_provider.BaostockProvider._ensure_login"
            ),
            patch("trade_krono_cli.data_providers.baostock_provider._bs") as mock_bs,
        ):
            mock_bs.query_stock_basic.return_value = mock_rs
            is_new, reason = provider.check_new_stock("sh.600001", today, min_listing_days=60)
            assert is_new is False

    def test_health_check_exception(self, provider):
        with patch.object(provider, "fetch_metadata", side_effect=RuntimeError("fail")):
            assert provider.health_check() is False


# ═══════════════════════════════════════════════════════
# AkShareProvider 测试（需要 akshare 包）
# ═══════════════════════════════════════════════════════

akshare_available = pytest.importorskip("akshare", reason="akshare not installed")


