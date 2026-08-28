"""
tests/test_data_providers.py — 多数据源抽象层测试（40+ 用例）。

覆盖：
  · base.py：KlineData / RealtimeQuote / StockMetadata 数据模型
  · baostock_provider.py：kline / metadata / ST / delisted / new-stock
  · akshare_provider.py：kline / quote（需要 akshare 包，未安装则跳过）
  · mootdx_provider.py：kline / quote（需要 mootdx 包，未安装则跳过）
  · tushare_provider.py：kline / metadata（需要 tushare 包，未安装则跳过）
  · factory.py：工厂实例化 / 降级路由 / 健康检查 / merged fetch
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from trade_krono_cli.data_providers.base import (
    DataProvider,
    KlineData,
    RealtimeQuote,
    StockMetadata,
)
from trade_krono_cli.data_providers.factory import (
    DataProviderFactory,
    get_data_factory,
    reset_data_factory,
)

# ═══════════════════════════════════════════════════════
# 固定 fixture
# ═══════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _reset_factory():
    """每个测试前重置工厂缓存。"""
    reset_data_factory()
    yield
    reset_data_factory()


@pytest.fixture
def sample_kline_data() -> KlineData:
    return KlineData(
        timestamps=[datetime(2026, 8, 1), datetime(2026, 8, 4)],
        open=[100.0, 102.0],
        high=[103.0, 104.0],
        low=[99.0, 101.0],
        close=[101.0, 103.0],
        volume=[1e6, 1.2e6],
        amount=[1e8, 1.2e8],
    )


@pytest.fixture
def sample_quote() -> RealtimeQuote:
    return RealtimeQuote(
        ticker="sh.600519",
        price=1800.5,
        pe=28.5,
        pb=5.2,
        market_cap=22600.0,
        turnover=0.3,
        source="akshare",
    )


@pytest.fixture
def sample_metadata() -> StockMetadata:
    return StockMetadata(
        ticker="sh.600519",
        industry="白酒",
        industry_code="C16",
        pe_ttm=28.5,
        pb=5.2,
        ipo_date="1999-11-10",
        out_date=None,
        is_st=False,
        source="baostock",
    )


# ═══════════════════════════════════════════════════════
# KlineData 模型测试
# ═══════════════════════════════════════════════════════


class TestKlineData:
    def test_empty_kline(self):
        kd = KlineData()
        assert kd.is_empty
        assert kd.length == 0

    def test_non_empty_kline(self, sample_kline_data):
        assert not sample_kline_data.is_empty
        assert sample_kline_data.length == 2

    def test_to_dataframe(self, sample_kline_data):
        import pandas as pd

        df = sample_kline_data.to_dataframe()
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 2
        assert list(df.columns) == [
            "timestamps",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "amount",
        ]

    def test_from_dataframe(self, sample_kline_data):
        df = sample_kline_data.to_dataframe()
        kd2 = KlineData.from_dataframe(df)
        assert kd2.length == 2
        assert kd2.close[0] == 101.0
        assert kd2.close[1] == 103.0

    def test_from_dataframe_roundtrip(self, sample_kline_data):
        df = sample_kline_data.to_dataframe()
        kd2 = KlineData.from_dataframe(df)
        assert kd2.open == sample_kline_data.open
        assert kd2.high == sample_kline_data.high
        assert kd2.low == sample_kline_data.low
        assert kd2.close == sample_kline_data.close
        assert kd2.volume == sample_kline_data.volume
        assert kd2.amount == sample_kline_data.amount


# ═══════════════════════════════════════════════════════
# RealtimeQuote / StockMetadata 模型测试
# ═══════════════════════════════════════════════════════


class TestQuoteMetadata:
    def test_quote_defaults(self):
        q = RealtimeQuote()
        assert q.ticker == ""
        assert q.price is None
        assert q.source == ""

    def test_quote_with_values(self, sample_quote):
        assert sample_quote.ticker == "sh.600519"
        assert sample_quote.price == 1800.5
        assert sample_quote.source == "akshare"

    def test_metadata_defaults(self):
        m = StockMetadata()
        assert m.ticker == ""
        assert m.industry is None
        assert m.is_st is False

    def test_metadata_with_values(self, sample_metadata):
        assert sample_metadata.industry == "白酒"
        assert sample_metadata.is_st is False
        assert sample_metadata.ipo_date == "1999-11-10"


# ═══════════════════════════════════════════════════════
# DataProvider ABC 测试
# ═══════════════════════════════════════════════════════


class TestDataProviderABC:
    def test_cannot_instantiate(self):
        with pytest.raises(TypeError):
            DataProvider()

    def test_subclass_must_implement_fetch_kline(self):
        class Partial(DataProvider):
            name = "partial"

            def fetch_quote(self, ticker):
                return None

            def fetch_metadata(self, ticker):
                return None

        with pytest.raises(TypeError):
            Partial()

    def test_subclass_must_implement_all_methods(self):
        class Concrete(DataProvider):
            name = "concrete"

            def fetch_kline(self, ticker, start, end, frequency="d", adjustflag="1"):
                return KlineData()

            def fetch_quote(self, ticker):
                return RealtimeQuote()

            def fetch_metadata(self, ticker):
                return StockMetadata()

        c = Concrete()
        assert c.name == "concrete"
        assert c.supports_kline is True
        assert c.supports_quote is True
        assert c.supports_metadata is True


# ═══════════════════════════════════════════════════════
# BaostockProvider 测试
# ═══════════════════════════════════════════════════════


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


class TestAkShareProvider:
    @pytest.fixture
    def provider(self):
        from trade_krono_cli.data_providers.akshare_provider import AkShareProvider

        return AkShareProvider()

    def test_name(self, provider):
        assert provider.name == "akshare"
        assert provider.supports_kline is True
        assert provider.supports_quote is True
        assert provider.supports_metadata is False

    def test_ticker_conversion(self, provider):
        assert provider._ticker_to_ak("sh.600519") == "600519"
        assert provider._ticker_to_ak("sz.000001") == "000001"
        assert provider._ak_to_ticker("600519") == "sh.600519"
        assert provider._ak_to_ticker("000001") == "sz.000001"

    def test_fetch_kline_import_error(self, provider):
        with patch.object(
            __import__(
                "trade_krono_cli.data_providers.akshare_provider", fromlist=["AkShareProvider"]
            ).AkShareProvider,
            "_ensure_import",
            side_effect=RuntimeError("not installed"),
        ):
            result = provider.fetch_kline("sh.600519", "2026-01-01", "2026-08-13")
            assert result is None

    def test_fetch_metadata_returns_none(self, provider):
        assert provider.fetch_metadata("sh.600519") is None

    def test_health_check_success(self, provider):
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
            }
        )
        from trade_krono_cli.data_providers.akshare_provider import AkShareProvider

        with patch.object(AkShareProvider, "_ensure_import"):
            with patch.object(provider, "_ak", create=True) as mock_ak:
                mock_ak.stock_zh_a_hist.return_value = mock_df
                assert provider.health_check() is True

    def test_health_check_failure(self, provider):
        from trade_krono_cli.data_providers.akshare_provider import AkShareProvider

        with patch.object(AkShareProvider, "_ensure_import", side_effect=Exception("fail")):
            assert provider.health_check() is False


# ═══════════════════════════════════════════════════════
# MootDxProvider 测试（需要 mootdx 包）
# ═══════════════════════════════════════════════════════

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
        import pandas as pd

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
        import pandas as pd

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


class TestTushareProvider:
    @pytest.fixture
    def provider(self):
        from trade_krono_cli.data_providers.tushare_provider import TushareProvider

        return TushareProvider()

    def test_name(self, provider):
        assert provider.name == "tushare"
        assert provider.supports_kline is True
        assert provider.supports_quote is True
        assert provider.supports_metadata is True

    def test_fetch_kline_success(self, provider):
        import pandas as pd

        mock_df = pd.DataFrame(
            {
                "trade_date": ["20260801", "20260804"],
                "open": [100.0, 102.0],
                "high": [103.0, 104.0],
                "low": [99.0, 101.0],
                "close": [101.0, 103.0],
                "vol": [1e6, 1.2e6],
                "amount": [1e8, 1.2e8],
            }
        )
        mock_ts = MagicMock()
        mock_ts.pro_bar.return_value = mock_df

        with patch.dict("os.environ", {"TUSHARE_TOKEN": "fake_token"}):
            with patch.object(provider.__class__, "_ts", mock_ts):
                result = provider.fetch_kline("sh.600519", "2026-01-01", "2026-08-13")
                assert result is not None
                assert result.length == 2

    def test_fetch_metadata_success(self, provider):
        import pandas as pd

        mock_df = pd.DataFrame(
            {
                "ts_code": ["600519.SH"],
                "name": ["贵州茅台"],
                "industry": ["白酒"],
                "list_date": ["1999-11-10"],
                "delist_date": [None],
            }
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

    def test_fetch_metadata_st_stock(self, provider):
        import pandas as pd

        mock_df = pd.DataFrame(
            {
                "ts_code": ["601234.SH"],
                "name": ["*ST某某"],
                "industry": ["机械"],
                "list_date": ["20200101"],
                "delist_date": [None],
            }
        )
        mock_ts = MagicMock()
        mock_ts.stock_basic.return_value = mock_df

        with patch.dict("os.environ", {"TUSHARE_TOKEN": "fake_token"}):
            with patch.object(provider.__class__, "_ts", mock_ts):
                meta = provider.fetch_metadata("sh.601234")
                assert meta is not None
                assert meta.is_st is True

    def test_health_check_success(self, provider):
        import pandas as pd

        mock_df = pd.DataFrame({"ts_code": ["600519.SH"]})
        mock_ts = MagicMock()
        mock_ts.stock_basic.return_value = mock_df

        with patch.dict("os.environ", {"TUSHARE_TOKEN": "fake_token"}):
            with patch.object(provider.__class__, "_ts", mock_ts):
                assert provider.health_check() is True

    def test_health_check_failure(self, provider):
        mock_ts = MagicMock()
        mock_ts.stock_basic.side_effect = Exception("fail")

        with patch.dict("os.environ", {"TUSHARE_TOKEN": "fake_token"}):
            with patch.object(provider.__class__, "_ts", mock_ts):
                assert provider.health_check() is False


# ═══════════════════════════════════════════════════════
# DataProviderFactory 测试
# ═══════════════════════════════════════════════════════


class TestDataProviderFactory:
    def test_default_chain(self):
        factory = DataProviderFactory()
        assert factory.primary == "baostock"
        assert factory.fallbacks == ["akshare", "mootdx", "tushare"]
        assert factory.provider_chain == ["baostock", "akshare", "mootdx", "tushare"]

    def test_custom_chain(self):
        factory = DataProviderFactory(primary="akshare", fallbacks=["baostock", "mootdx"])
        assert factory.provider_chain == ["akshare", "baostock", "mootdx"]

    def test_get_provider_unknown(self):
        factory = DataProviderFactory()
        assert factory.get_provider("unknown_source") is None

    def test_get_provider_registry_caching(self):
        factory = DataProviderFactory()
        cls1 = factory._get_provider_class("baostock")
        cls2 = factory._get_provider_class("baostock")
        assert cls1 is cls2

    def test_fetch_kline_fallback_chain(self):
        """主源失败时自动降级到备用源。"""
        factory = DataProviderFactory(primary="akshare", fallbacks=["baostock"])

        mock_kline = KlineData(
            timestamps=[datetime(2026, 8, 1)],
            open=[100.0],
            high=[101.0],
            low=[99.0],
            close=[100.5],
            volume=[1e6],
            amount=[1e8],
        )

        from trade_krono_cli.data_providers.baostock_provider import BaostockProvider

        bs_provider = BaostockProvider()

        with patch.object(factory, "get_provider", side_effect=[None, bs_provider]):
            with patch.object(bs_provider, "fetch_kline", return_value=mock_kline):
                with patch.object(bs_provider, "health_check", return_value=True):
                    result = factory.fetch_kline("sh.600519", "2026-01-01", "2026-08-13")
                    assert result is not None
                    assert result.length == 1

    def test_fetch_quote_fallback(self):
        factory = DataProviderFactory(primary="mootdx", fallbacks=["akshare"])
        mock_quote = RealtimeQuote(ticker="sh.600519", price=1800.0, source="akshare")

        from trade_krono_cli.data_providers.akshare_provider import AkShareProvider

        ak_provider = AkShareProvider()

        with patch.object(factory, "get_provider", side_effect=[None, ak_provider]):
            with patch.object(ak_provider, "fetch_quote", return_value=mock_quote):
                with patch.object(ak_provider, "health_check", return_value=True):
                    result = factory.fetch_quote("sh.600519")
                    assert result is not None
                    assert result.price == 1800.0

    def test_fetch_metadata_fallback(self):
        factory = DataProviderFactory(primary="mootdx", fallbacks=["tushare"])
        mock_meta = StockMetadata(ticker="sh.600519", industry="白酒", source="tushare")

        from trade_krono_cli.data_providers.tushare_provider import TushareProvider

        ts_provider = TushareProvider()

        with patch.object(factory, "get_provider", side_effect=[None, ts_provider]):
            with patch.object(ts_provider, "fetch_metadata", return_value=mock_meta):
                with patch.object(ts_provider, "health_check", return_value=True):
                    result = factory.fetch_metadata("sh.600519")
                    assert result is not None
                    assert result.industry == "白酒"

    def test_fetch_merged(self):
        factory = DataProviderFactory(primary="akshare", fallbacks=["baostock"])
        mock_kline = KlineData(
            timestamps=[datetime(2026, 8, 1)],
            open=[100.0],
            high=[101.0],
            low=[99.0],
            close=[100.5],
            volume=[1e6],
            amount=[1e8],
        )
        mock_meta = StockMetadata(ticker="sh.600519", industry="白酒", source="baostock")

        from trade_krono_cli.data_providers.baostock_provider import BaostockProvider

        bs_provider = BaostockProvider()

        calls = []

        def mock_get_provider(name):
            calls.append(name)
            if name == "akshare":
                return None
            return bs_provider

        with patch.object(factory, "get_provider", side_effect=mock_get_provider):
            with patch.object(bs_provider, "fetch_kline", return_value=mock_kline):
                with patch.object(bs_provider, "fetch_metadata", return_value=mock_meta):
                    with patch.object(bs_provider, "fetch_quote", return_value=None):
                        with patch.object(bs_provider, "health_check", return_value=True):
                            result = factory.fetch_merged("sh.600519", "2026-01-01", "2026-08-13")
                            assert result["kline"] is not None
                            assert result["metadata"] is not None

    def test_available_providers(self):
        factory = DataProviderFactory()
        available = factory.available_providers()
        assert "baostock" in available

    def test_health_check_all(self):
        factory = DataProviderFactory(primary="akshare", fallbacks=["baostock"])
        result = factory.health_check_all()
        assert "akshare" in result
        assert "baostock" in result

    def test_reset_cache(self):
        factory = DataProviderFactory()
        factory.get_provider("baostock")
        assert "baostock" in DataProviderFactory._instance_cache
        factory.reset_cache()
        assert "baostock" not in DataProviderFactory._instance_cache

    def test_get_providers_filters_unavailable(self):
        factory = DataProviderFactory(primary="baostock", fallbacks=["unknown_src"])
        providers = factory.get_providers(["baostock", "unknown_src"])
        names = [p.name for p in providers]
        assert "baostock" in names
        assert "unknown_src" not in names

    def test_factory_singleton(self):
        """get_data_factory() 返回同一实例。"""
        reset_data_factory()
        f1 = get_data_factory()
        f2 = get_data_factory()
        assert f1 is f2

    def test_all_providers_fail_returns_none(self):
        """所有 Provider 均不可用时应返回 None。"""
        factory = DataProviderFactory(primary="akshare", fallbacks=[])
        with patch.object(factory, "get_provider", return_value=None):
            result = factory.fetch_kline("sh.600519", "2026-01-01", "2026-08-13")
            assert result is None


# ═══════════════════════════════════════════════════════
# 边缘情况测试
# ═══════════════════════════════════════════════════════


class TestEdgeCases:
    def test_kline_data_nan_protection(self):
        """to_dataframe 在空数据时不会崩溃。"""
        kd = KlineData()
        df = kd.to_dataframe()
        assert len(df) == 0

    def test_config_data_provider_validation(self):
        """无效的 data_provider 值应被校验器拒绝。"""
        from pathlib import Path
        from types import SimpleNamespace

        from trade_krono_cli.config_validator import validate_settings

        s = SimpleNamespace(
            project_root=Path("/tmp"),
            cache_dir=Path("/tmp/cache"),
            results_dir=Path("/tmp/results"),
            tradingagents_root=Path("/tmp/ta"),
            kronos_root=Path("/tmp/kronos"),
            llm_provider="deepseek",
            deep_think_llm="x",
            quick_think_llm="x",
            backend_url=None,
            max_debate_rounds=1,
            max_risk_discuss_rounds=1,
            checkpoint_enabled=True,
            output_language="Chinese",
            kronos_model="x",
            kronos_tokenizer="x",
            kronos_device="cpu",
            kronos_lookback=400,
            kronos_pred_len=30,
            kronos_sample_count=5,
            kronos_T=1.0,
            kronos_top_p=0.9,
            kronos_use_sample_confidence=False,
            kronos_batch_size=8,
            default_min_confidence=55.0,
            default_allowed_signals=["BUY"],
            filter_market_cap_range="",
            filter_industry_whitelist="",
            filter_industry_blacklist="",
            filter_pe_range="",
            filter_pb_range="",
            filter_max_risk_score="",
            filter_min_volume_ratio="",
            filter_exclude_st=True,
            filter_skip_suspended=True,
            filter_skip_new_stock=True,
            filter_new_stock_min_days=60,
            filter_kline_min_completeness=0.85,
            filter_abnormality_risk_boost_enabled=True,
            baostock_sleep_sec=1.0,
            memory_log_path=Path("/tmp/log.jsonl"),
            data_provider="invalid_source",
            data_fallback="",
            akshare_enabled=True,
            mootdx_enabled=True,
            scoring_strategy="linear",
            risk_boost_strategy="fixed_boost",
            risk_boost_multiplier=1.0,
            risk_boost_diminishing_power=0.5,
            retry_max_attempts=3,
            retry_base_delay=2.0,
            retry_jitter=True,
            retry_rate_limit_backoff=True,
            retry_rate_limit_max_wait=60.0,
            degrade_mode="strict",
            ta_cache_fallback_enabled=False,
            ta_cache_max_age_days=7,
        )
        errors, warnings = validate_settings(s)
        assert any("DATA_PROVIDER" in e for e in errors)

    def test_config_data_fallback_includes_primary(self):
        """data_fallback 不能包含 primary。"""
        from pathlib import Path
        from types import SimpleNamespace

        from trade_krono_cli.config_validator import validate_settings

        s = SimpleNamespace(
            project_root=Path("/tmp"),
            cache_dir=Path("/tmp/cache"),
            results_dir=Path("/tmp/results"),
            tradingagents_root=Path("/tmp/ta"),
            kronos_root=Path("/tmp/kronos"),
            llm_provider="deepseek",
            deep_think_llm="x",
            quick_think_llm="x",
            backend_url=None,
            max_debate_rounds=1,
            max_risk_discuss_rounds=1,
            checkpoint_enabled=True,
            output_language="Chinese",
            kronos_model="x",
            kronos_tokenizer="x",
            kronos_device="cpu",
            kronos_lookback=400,
            kronos_pred_len=30,
            kronos_sample_count=5,
            kronos_T=1.0,
            kronos_top_p=0.9,
            kronos_use_sample_confidence=False,
            kronos_batch_size=8,
            default_min_confidence=55.0,
            default_allowed_signals=["BUY"],
            filter_market_cap_range="",
            filter_industry_whitelist="",
            filter_industry_blacklist="",
            filter_pe_range="",
            filter_pb_range="",
            filter_max_risk_score="",
            filter_min_volume_ratio="",
            filter_exclude_st=True,
            filter_skip_suspended=True,
            filter_skip_new_stock=True,
            filter_new_stock_min_days=60,
            filter_kline_min_completeness=0.85,
            filter_abnormality_risk_boost_enabled=True,
            baostock_sleep_sec=1.0,
            memory_log_path=Path("/tmp/log.jsonl"),
            data_provider="baostock",
            data_fallback="baostock,akshare",
            akshare_enabled=True,
            mootdx_enabled=True,
            scoring_strategy="linear",
            risk_boost_strategy="fixed_boost",
            risk_boost_multiplier=1.0,
            risk_boost_diminishing_power=0.5,
            retry_max_attempts=3,
            retry_base_delay=2.0,
            retry_jitter=True,
            retry_rate_limit_backoff=True,
            retry_rate_limit_max_wait=60.0,
            degrade_mode="strict",
            ta_cache_fallback_enabled=False,
            ta_cache_max_age_days=7,
        )
        errors, warnings = validate_settings(s)
        assert any("DATA_FALLBACK" in e and "不能包含" in e for e in errors)

    def test_factory_provider_class_unknown(self):
        """未知 Provider 名称应返回 None。"""
        factory = DataProviderFactory()
        assert factory._get_provider_class("nonexistent") is None

    def test_fetch_kline_empty_result(self):
        """Provider 返回空 KlineData 时应视为失败并继续降级。"""
        factory = DataProviderFactory(primary="baostock", fallbacks=[])
        from trade_krono_cli.data_providers.baostock_provider import BaostockProvider

        bs = BaostockProvider()
        empty_kline = KlineData()  # 空

        with patch.object(factory, "get_provider", return_value=bs):
            with patch.object(bs, "fetch_kline", return_value=empty_kline):
                with patch.object(bs, "health_check", return_value=True):
                    result = factory.fetch_kline("sh.600519", "2026-01-01", "2026-08-13")
                    assert result is None  # 空结果视为失败
