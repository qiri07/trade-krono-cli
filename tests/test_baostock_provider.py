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

    def test_name(self, provider) -> None:
        assert provider.name == "baostock"
        assert provider.supports_kline is True
        assert provider.supports_quote is False
        assert provider.supports_metadata is True

    def test_fetch_kline_import_error(self, provider) -> None:
        with (
            patch(
                "trade_krono_cli.data_providers.baostock_provider.BaostockProvider._ensure_import",
                side_effect=RuntimeError("baostock not installed"),
            ),
            pytest.raises(RuntimeError, match="baostock not installed"),
        ):
            provider.fetch_kline("sh.600519", "2026-01-01", "2026-08-13")

    def test_fetch_kline_success(self, provider) -> None:
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
                "trade_krono_cli.data_providers.baostock_provider.BaostockProvider._ensure_import",
            ),
            patch(
                "trade_krono_cli.data_providers.baostock_provider._bs_semaphore",
                MagicMock(),
            ),
            patch(
                "trade_krono_cli.data_providers.baostock_provider.BaostockProvider._ensure_login",
            ),
            patch("trade_krono_cli.data_providers.baostock_provider._bs") as mock_bs,
        ):
            mock_bs.query_history_k_data_plus.return_value = mock_rs
            result = provider.fetch_kline("sh.600519", "2026-01-01", "2026-08-13")
            assert result is not None
            assert result.length == 2
            assert result.close[1] == 103.0

    def test_fetch_kline_error_code(self, provider) -> None:
        mock_rs = MagicMock()
        mock_rs.error_code = "runerror"
        mock_rs.error_msg = "query failed"

        with (
            patch(
                "trade_krono_cli.data_providers.baostock_provider.BaostockProvider._ensure_import",
            ),
            patch(
                "trade_krono_cli.data_providers.baostock_provider._bs_semaphore",
                MagicMock(),
            ),
            patch(
                "trade_krono_cli.data_providers.baostock_provider.BaostockProvider._ensure_login",
            ),
            patch("trade_krono_cli.data_providers.baostock_provider._bs") as mock_bs,
        ):
            mock_bs.query_history_k_data_plus.return_value = mock_rs
            result = provider.fetch_kline("sh.600519", "2026-01-01", "2026-08-13")
            assert result is None

    def test_fetch_quote_returns_none(self, provider) -> None:
        assert provider.fetch_quote("sh.600519") is None

    def test_fetch_metadata_success(self, provider) -> None:
        mock_rs = MagicMock()
        mock_rs.error_code = "0"
        mock_rs.get_row_data.return_value = ("sh.600519", "贵州茅台", "1999-11-10", None)
        mock_rs.next.side_effect = [True, False]

        with (
            patch(
                "trade_krono_cli.data_providers.baostock_provider.BaostockProvider._ensure_import",
            ),
            patch(
                "trade_krono_cli.data_providers.baostock_provider.BaostockProvider._ensure_login",
            ),
            patch("trade_krono_cli.data_providers.baostock_provider._bs") as mock_bs,
        ):
            mock_bs.query_stock_basic.return_value = mock_rs
            meta = provider.fetch_metadata("sh.600519")
            assert meta is not None
            assert meta.ticker == "sh.600519"
            assert meta.ipo_date == "1999-11-10"
            assert meta.is_st is False

    def test_fetch_metadata_st_stock(self, provider) -> None:
        mock_rs = MagicMock()
        mock_rs.error_code = "0"
        mock_rs.get_row_data.return_value = ("sh.601234", "*ST某某", "2020-01-01", None)
        mock_rs.next.side_effect = [True, False]

        with (
            patch(
                "trade_krono_cli.data_providers.baostock_provider.BaostockProvider._ensure_import",
            ),
            patch(
                "trade_krono_cli.data_providers.baostock_provider.BaostockProvider._ensure_login",
            ),
            patch("trade_krono_cli.data_providers.baostock_provider._bs") as mock_bs,
        ):
            mock_bs.query_stock_basic.return_value = mock_rs
            meta = provider.fetch_metadata("sh.601234")
            assert meta is not None
            assert meta.is_st is True

    def test_check_st_status_cached(self, provider) -> None:
        mock_rs = MagicMock()
        mock_rs.error_code = "0"
        mock_rs.get_row_data.return_value = ("sh.601234", "ST某某", "2020-01-01", None)
        mock_rs.next.side_effect = [True, False]

        with (
            patch(
                "trade_krono_cli.data_providers.baostock_provider.BaostockProvider._ensure_import",
            ),
            patch(
                "trade_krono_cli.data_providers.baostock_provider.BaostockProvider._ensure_login",
            ),
            patch("trade_krono_cli.data_providers.baostock_provider._bs") as mock_bs,
        ):
            mock_bs.query_stock_basic.return_value = mock_rs
            assert provider.check_st_status("sh.601234") is True
            # 第二次调用应命中缓存，不调用 API
            with patch.object(mock_bs, "query_stock_basic") as mock_q:
                assert provider.check_st_status("sh.601234") is True
                mock_q.assert_not_called()

    def test_check_delisted_future(self, provider) -> None:
        future_date = (datetime.now() + __import__("datetime").timedelta(days=365)).strftime(
            "%Y-%m-%d",
        )
        mock_rs = MagicMock()
        mock_rs.error_code = "0"
        mock_rs.get_row_data.return_value = ("sh.600001", "某股", "2020-01-01", future_date)
        mock_rs.next.side_effect = [True, False]

        with (
            patch(
                "trade_krono_cli.data_providers.baostock_provider.BaostockProvider._ensure_import",
            ),
            patch(
                "trade_krono_cli.data_providers.baostock_provider.BaostockProvider._ensure_login",
            ),
            patch("trade_krono_cli.data_providers.baostock_provider._bs") as mock_bs,
        ):
            mock_bs.query_stock_basic.return_value = mock_rs
            assert provider.check_delisted("sh.600001") is False

    def test_check_delisted_past(self, provider) -> None:
        past_date = "2020-01-01"
        mock_rs = MagicMock()
        mock_rs.error_code = "0"
        mock_rs.get_row_data.return_value = ("sh.600001", "某股", "2010-01-01", past_date)
        mock_rs.next.side_effect = [True, False]

        with (
            patch(
                "trade_krono_cli.data_providers.baostock_provider.BaostockProvider._ensure_import",
            ),
            patch(
                "trade_krono_cli.data_providers.baostock_provider.BaostockProvider._ensure_login",
            ),
            patch("trade_krono_cli.data_providers.baostock_provider._bs") as mock_bs,
        ):
            mock_bs.query_stock_basic.return_value = mock_rs
            assert provider.check_delisted("sh.600001") is True

    def test_check_new_stock_yes(self, provider) -> None:
        recent_ipo = (datetime.now() - __import__("datetime").timedelta(days=30)).strftime(
            "%Y-%m-%d",
        )
        mock_rs = MagicMock()
        mock_rs.error_code = "0"
        mock_rs.get_row_data.return_value = ("sh.600001", "某股", recent_ipo, None)
        mock_rs.next.side_effect = [True, False]

        today = datetime.now().strftime("%Y-%m-%d")
        with (
            patch(
                "trade_krono_cli.data_providers.baostock_provider.BaostockProvider._ensure_import",
            ),
            patch(
                "trade_krono_cli.data_providers.baostock_provider.BaostockProvider._ensure_login",
            ),
            patch("trade_krono_cli.data_providers.baostock_provider._bs") as mock_bs,
        ):
            mock_bs.query_stock_basic.return_value = mock_rs
            is_new, _reason = provider.check_new_stock("sh.600001", today, min_listing_days=60)
            assert is_new is True

    def test_check_new_stock_no(self, provider) -> None:
        old_ipo = "2010-01-01"
        mock_rs = MagicMock()
        mock_rs.error_code = "0"
        mock_rs.get_row_data.return_value = ("sh.600001", "某股", old_ipo, None)
        mock_rs.next.side_effect = [True, False]

        today = datetime.now().strftime("%Y-%m-%d")
        with (
            patch(
                "trade_krono_cli.data_providers.baostock_provider.BaostockProvider._ensure_import",
            ),
            patch(
                "trade_krono_cli.data_providers.baostock_provider.BaostockProvider._ensure_login",
            ),
            patch("trade_krono_cli.data_providers.baostock_provider._bs") as mock_bs,
        ):
            mock_bs.query_stock_basic.return_value = mock_rs
            is_new, _reason = provider.check_new_stock("sh.600001", today, min_listing_days=60)
            assert is_new is False

    def test_health_check_exception(self, provider) -> None:
        with patch.object(provider, "fetch_metadata", side_effect=RuntimeError("fail")):
            assert provider.health_check() is False


# ═══════════════════════════════════════════════════════
# AkShareProvider 测试（需要 akshare 包）
# ═══════════════════════════════════════════════════════

akshare_available = pytest.importorskip("akshare", reason="akshare not installed")


class TestBaostockProviderModuleState:
    """Test module-level cleanup, import, login, and logout edge paths."""

    @pytest.fixture(autouse=True)
    def _reset_bs_globals(self):
        """Reset baostock module-level globals before each test."""
        from trade_krono_cli.data_providers import baostock_provider as bp

        bp._bs = None
        bp._HAS_BS = False
        bp._bs_logged_in = False
        yield
        bp._bs = None
        bp._HAS_BS = False
        bp._bs_logged_in = False

    def test_cleanup_bs_on_exit_logout_exception(self) -> None:
        """_cleanup_bs_on_exit should swallow logout exceptions and reset state."""
        from unittest.mock import MagicMock

        from trade_krono_cli.data_providers import baostock_provider as bp

        mock_bs = MagicMock()
        mock_bs.logout.side_effect = RuntimeError("logout failed")
        bp._bs = mock_bs
        bp._HAS_BS = True
        bp._bs_logged_in = True

        bp._cleanup_bs_on_exit()

        assert bp._bs_logged_in is False
        assert bp._HAS_BS is False
        assert bp._bs is None

    def test_ensure_import_import_error(self) -> None:
        """_ensure_import should raise RuntimeError when baostock is not installed."""
        from unittest.mock import patch

        from trade_krono_cli.data_providers import baostock_provider as bp

        bp._HAS_BS = False
        bp._bs = None

        provider = bp.BaostockProvider()
        with (
            patch("builtins.__import__", side_effect=ImportError("no baostock")),
            pytest.raises(RuntimeError, match="baostock 未安装"),
        ):
            provider._ensure_import()

    def test_ensure_login_failure(self) -> None:
        """_ensure_login should raise RuntimeError when login returns error_code != 0."""
        from unittest.mock import MagicMock

        from trade_krono_cli.data_providers import baostock_provider as bp

        bp._HAS_BS = True
        bp._bs_logged_in = False
        bp._bs = MagicMock()
        bp._bs.login.return_value = MagicMock(error_code="1", error_msg="login failed")

        provider = bp.BaostockProvider()
        with pytest.raises(RuntimeError, match="baostock 登录失败"):
            provider._ensure_login()

    def test_logout_exception(self) -> None:
        """_logout should swallow logout exceptions and reset _bs_logged_in."""
        from unittest.mock import MagicMock

        from trade_krono_cli.data_providers import baostock_provider as bp

        bp._bs = MagicMock()
        bp._bs.logout.side_effect = RuntimeError("logout failed")
        bp._bs_logged_in = True

        provider = bp.BaostockProvider()
        provider._logout()

        assert bp._bs_logged_in is False

    def test_query_stock_basic_error_code(self) -> None:
        """_query_stock_basic should return [] when error_code != 0."""
        from unittest.mock import MagicMock, patch

        from trade_krono_cli.data_providers import baostock_provider as bp

        mock_rs = MagicMock()
        mock_rs.error_code = "1"
        mock_rs.error_msg = "query failed"

        with (
            patch("trade_krono_cli.data_providers.baostock_provider._bs_semaphore", MagicMock()),
            patch.object(bp.BaostockProvider, "_ensure_login"),
            patch("trade_krono_cli.data_providers.baostock_provider._bs") as mock_bs,
        ):
            mock_bs.query_stock_basic.return_value = mock_rs
            provider = bp.BaostockProvider()
            result = provider._query_stock_basic("sh.600519")
            assert result == []


class TestBaostockProviderFetchKlineEdge:
    """Test fetch_kline retry, empty, and exception paths."""

    def test_fetch_kline_session_expired_retry(self) -> None:
        """fetch_kline should re-login and retry when error_code == 10001001."""
        from unittest.mock import MagicMock, patch

        from trade_krono_cli.data_providers import baostock_provider as bp

        provider = bp.BaostockProvider()

        # First response: session expired
        rs1 = MagicMock()
        rs1.error_code = "10001001"
        rs1.error_msg = "no login"

        # Second response: success
        rs2 = MagicMock()
        rs2.error_code = "0"
        rs2.fields = ["date", "open", "high", "low", "close", "volume", "amount"]
        rs2.get_row_data.side_effect = [
            ("2026-08-01", 100, 103, 99, 101, 1000000, 100000000),
        ]
        rs2.next.side_effect = [True, False]

        with (
            patch.object(bp.BaostockProvider, "_ensure_import"),
            patch("trade_krono_cli.data_providers.baostock_provider._bs_semaphore", MagicMock()),
            patch.object(bp.BaostockProvider, "_ensure_login"),
            patch("trade_krono_cli.data_providers.baostock_provider._bs") as mock_bs,
        ):
            mock_bs.query_history_k_data_plus.side_effect = [rs1, rs2]
            result = provider.fetch_kline("sh.600519", "2026-08-01", "2026-08-04")
            assert result is not None
            assert result.length == 1
            assert result.close[0] == 101.0

    def test_fetch_kline_error_after_retry(self) -> None:
        """fetch_kline should return None when error persists after session retry."""
        from unittest.mock import MagicMock, patch

        from trade_krono_cli.data_providers import baostock_provider as bp

        provider = bp.BaostockProvider()

        rs1 = MagicMock()
        rs1.error_code = "10001001"
        rs1.error_msg = "no login"

        rs2 = MagicMock()
        rs2.error_code = "1"
        rs2.error_msg = "still failed"

        with (
            patch.object(bp.BaostockProvider, "_ensure_import"),
            patch("trade_krono_cli.data_providers.baostock_provider._bs_semaphore", MagicMock()),
            patch.object(bp.BaostockProvider, "_ensure_login"),
            patch("trade_krono_cli.data_providers.baostock_provider._bs") as mock_bs,
        ):
            mock_bs.query_history_k_data_plus.side_effect = [rs1, rs2]
            result = provider.fetch_kline("sh.600519", "2026-08-01", "2026-08-04")
            assert result is None

    def test_fetch_kline_empty_rows(self) -> None:
        """fetch_kline should return None when query returns no rows."""
        from unittest.mock import MagicMock, patch

        from trade_krono_cli.data_providers import baostock_provider as bp

        provider = bp.BaostockProvider()

        rs = MagicMock()
        rs.error_code = "0"
        rs.next.side_effect = [False]  # no rows

        with (
            patch.object(bp.BaostockProvider, "_ensure_import"),
            patch("trade_krono_cli.data_providers.baostock_provider._bs_semaphore", MagicMock()),
            patch.object(bp.BaostockProvider, "_ensure_login"),
            patch("trade_krono_cli.data_providers.baostock_provider._bs") as mock_bs,
        ):
            mock_bs.query_history_k_data_plus.return_value = rs
            result = provider.fetch_kline("sh.600519", "2026-08-01", "2026-08-04")
            assert result is None

    def test_fetch_kline_exception(self) -> None:
        """fetch_kline should return None when query raises an exception."""
        from unittest.mock import MagicMock, patch

        from trade_krono_cli.data_providers import baostock_provider as bp

        provider = bp.BaostockProvider()

        with (
            patch.object(bp.BaostockProvider, "_ensure_import"),
            patch("trade_krono_cli.data_providers.baostock_provider._bs_semaphore", MagicMock()),
            patch.object(bp.BaostockProvider, "_ensure_login"),
            patch("trade_krono_cli.data_providers.baostock_provider._bs") as mock_bs,
        ):
            mock_bs.query_history_k_data_plus.side_effect = RuntimeError("network error")
            result = provider.fetch_kline("sh.600519", "2026-08-01", "2026-08-04")
            assert result is None


class TestBaostockProviderBuildKlineData:
    """Test _build_kline_data edge paths."""

    def test_build_kline_data_empty_after_dropna(self) -> None:
        """_build_kline_data should return None when all OHLC rows are missing."""
        from trade_krono_cli.data_providers import baostock_provider as bp

        rows = [
            ("2026-08-01", None, None, None, None, 1000000, 100000000),
        ]
        fields = ["date", "open", "high", "low", "close", "volume", "amount"]
        result = bp.BaostockProvider._build_kline_data("sh.600519", rows, fields)
        assert result is None


class TestBaostockProviderFetchMetadataEdge:
    """Test fetch_metadata edge paths."""

    def test_fetch_metadata_no_rows(self) -> None:
        """fetch_metadata should return None when _query_stock_basic returns empty."""
        from unittest.mock import patch

        from trade_krono_cli.data_providers import baostock_provider as bp

        provider = bp.BaostockProvider()
        with patch.object(provider, "_query_stock_basic", return_value=[]):
            result = provider.fetch_metadata("sh.600519")
            assert result is None


class TestBaostockProviderCheckDelistedEdge:
    """Test check_delisted edge paths."""

    def test_check_delisted_value_error(self) -> None:
        """check_delisted should return False when out_date is unparseable."""
        from unittest.mock import MagicMock, patch

        from trade_krono_cli.data_providers import baostock_provider as bp

        provider = bp.BaostockProvider()
        meta = MagicMock()
        meta.out_date = "not-a-date"
        with patch.object(provider, "fetch_metadata", return_value=meta):
            result = provider.check_delisted("sh.600519")
            assert result is False


class TestBaostockProviderCheckNewStockEdge:
    """Test check_new_stock edge paths."""

    def test_check_new_stock_value_error(self) -> None:
        """check_new_stock should return (False, '') when ipo_date is unparseable."""
        from unittest.mock import MagicMock, patch

        from trade_krono_cli.data_providers import baostock_provider as bp

        provider = bp.BaostockProvider()
        meta = MagicMock()
        meta.ipo_date = "not-a-date"
        with patch.object(provider, "fetch_metadata", return_value=meta):
            is_new, reason = provider.check_new_stock("sh.600519", "2026-08-01")
            assert is_new is False
            assert reason == ""


class TestBaostockProviderHealthCheckEdge:
    """Test health_check edge paths."""

    def test_health_check_no_metadata(self) -> None:
        """health_check should return False when fetch_metadata returns None."""
        from unittest.mock import patch

        from trade_krono_cli.data_providers import baostock_provider as bp

        provider = bp.BaostockProvider()
        with patch.object(provider, "fetch_metadata", return_value=None):
            result = provider.health_check()
            assert result is False
