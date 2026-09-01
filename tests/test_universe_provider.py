"""
Tests for universe/provider.py — covers previously uncovered lines:
  - Base _safe_float NaN / inf edge cases
  - AkshareUniverseProvider.get_universe() success path
  - AkshareUniverseProvider._safe_float (class-level override)
  - MootDxUniverseProvider market_cap fill path
  - TongHuaShunUniverseProvider full get_universe path
  - TongHuaShunUniverseProvider._thscode_to_ticker
  - get_universe_provider factory fallbacks
"""

from __future__ import annotations

from types import ModuleType
from unittest.mock import patch

from trade_krono_cli.universe.provider import (
    AkshareUniverseProvider,
    MootDxUniverseProvider,
    TongHuaShunUniverseProvider,
    UniverseTicket,
    get_universe_provider,
)

# ═════════════════════════════════════════════════════════════════════════════
#  Base class _safe_float edge cases
# ═════════════════════════════════════════════════════════════════════════════


class TestSafeFloat:
    """Base UniverseProvider._safe_float static method edge cases."""

    def test_none_returns_none(self):
        assert AkshareUniverseProvider._safe_float(None) is None

    def test_string_number(self):
        assert AkshareUniverseProvider._safe_float("3.14") == 3.14

    def test_int(self):
        assert AkshareUniverseProvider._safe_float(42) == 42.0

    def test_nan_returns_none(self):
        assert AkshareUniverseProvider._safe_float(float("nan")) is None

    def test_pos_inf_returns_none(self):
        assert AkshareUniverseProvider._safe_float(float("inf")) is None

    def test_neg_inf_returns_none(self):
        assert AkshareUniverseProvider._safe_float(float("-inf")) is None

    def test_non_numeric_string_returns_none(self):
        assert AkshareUniverseProvider._safe_float("abc") is None

    def test_type_error_returns_none(self):
        assert AkshareUniverseProvider._safe_float({"a": 1}) is None


# ═════════════════════════════════════════════════════════════════════════════
#  AkshareUniverseProvider
# ═════════════════════════════════════════════════════════════════════════════


def _make_fake_akshare_df():
    """Create a fake akshare DataFrame for mocking."""
    rows = [
        {
            "代码": "600519",
            "名称": "贵州茅台",
            "最新价": 1800.0,
            "市盈率-动态": 38.5,
            "市净率": 9.2,
            "总市值": 22500.0,
            "量比": 1.2,
            "换手率": 0.5,
            "成交量": 50000.0,
            "行业": "白酒",
        },
        {
            "代码": "000858",
            "名称": "五粮液",
            "最新价": 150.0,
            "市盈率-动态": 20.0,
            "市净率": 3.5,
            "总市值": 5900.0,
            "量比": 0.9,
            "换手率": 1.1,
            "成交量": 120000.0,
            "行业": "白酒",
        },
    ]

    class FakeDF:
        empty = False

        def iterrows(self):
            for row in rows:
                yield None, row

        def __len__(self):
            return len(rows)

    return FakeDF()


class TestAkshareGetUniverse:
    """AkshareUniverseProvider.get_universe success path."""

    def test_get_universe_returns_tickets_with_all_fields(self):
        fake_df = _make_fake_akshare_df()
        fake_ak = ModuleType("akshare")
        fake_ak.stock_zh_a_spot_em = lambda: fake_df

        with patch.dict("sys.modules", {"akshare": fake_ak}):
            provider = AkshareUniverseProvider()
            tickets = provider.get_universe()

        assert len(tickets) == 2
        by_ticker = {t.ticker: t for t in tickets}
        assert by_ticker["sh.600519"].name == "贵州茅台"
        assert by_ticker["sh.600519"].price == 1800.0
        assert by_ticker["sh.600519"].pe == 38.5
        assert by_ticker["sh.600519"].pb == 9.2
        assert by_ticker["sh.600519"].market_cap == 22500.0
        assert by_ticker["sh.600519"].volume_ratio == 1.2
        assert by_ticker["sh.600519"].turnover_rate == 0.5
        assert by_ticker["sh.600519"].volume == 50000.0
        assert by_ticker["sh.600519"].industry == "白酒"
        assert by_ticker["sh.600519"].source == "akshare"

    def test_get_universe_skips_invalid_codes(self):
        """Codes shorter/longer than 6 digits are skipped."""
        rows = [{"代码": "123", "名称": "短码"}, {"代码": "600519", "名称": "茅台"}]

        class FakeDF:
            empty = False

            def iterrows(self):
                for row in rows:
                    yield None, row

            def __len__(self):
                return len(rows)

        fake_ak = ModuleType("akshare")
        fake_ak.stock_zh_a_spot_em = lambda: FakeDF()

        with patch.dict("sys.modules", {"akshare": fake_ak}):
            provider = AkshareUniverseProvider()
            tickets = provider.get_universe()

        assert len(tickets) == 1
        assert tickets[0].ticker == "sh.600519"

    def test_get_universe_empty_df_returns_empty(self):
        class FakeDF:
            empty = True

            def iterrows(self):
                return iter([])

            def __len__(self):
                return 0

        fake_ak = ModuleType("akshare")
        fake_ak.stock_zh_a_spot_em = lambda: FakeDF()

        with patch.dict("sys.modules", {"akshare": fake_ak}):
            provider = AkshareUniverseProvider()
            assert provider.get_universe() == []

    def test_get_universe_import_error_returns_empty(self):
        with patch.dict("sys.modules", {"akshare": None}):
            provider = AkshareUniverseProvider()
            assert provider.get_universe() == []

    def test_get_universe_api_error_returns_empty(self):
        fake_ak = ModuleType("akshare")

        def bad_api():
            raise RuntimeError("network timeout")

        fake_ak.stock_zh_a_spot_em = bad_api

        with patch.dict("sys.modules", {"akshare": fake_ak}):
            provider = AkshareUniverseProvider()
            assert provider.get_universe() == []

    def test_code_to_ticker_sh_prefix(self):
        assert AkshareUniverseProvider._code_to_ticker("600519") == "sh.600519"
        assert AkshareUniverseProvider._code_to_ticker("510050") == "sh.510050"
        assert AkshareUniverseProvider._code_to_ticker("900901") == "sh.900901"

    def test_code_to_ticker_sz_prefix(self):
        assert AkshareUniverseProvider._code_to_ticker("000858") == "sz.000858"
        assert AkshareUniverseProvider._code_to_ticker("300750") == "sz.300750"

    def test_code_to_ticker_empty_string(self):
        assert AkshareUniverseProvider._code_to_ticker("") == "sz."

    def test_class_safe_float_with_nan_inf(self):
        """AkshareUniverseProvider has its own _safe_float override."""
        assert AkshareUniverseProvider._safe_float(float("nan")) is None
        assert AkshareUniverseProvider._safe_float(float("inf")) is None
        assert AkshareUniverseProvider._safe_float(float("-inf")) is None


# ═════════════════════════════════════════════════════════════════════════════
#  MootDxUniverseProvider — market_cap fill path
# ═════════════════════════════════════════════════════════════════════════════
#  MootDxUniverseProvider — attribute tests
# ═════════════════════════════════════════════════════════════════════════════


class TestMootDxFetchQuotesBatch:
    """Test _fetch_quotes_batch helper."""

    def test_fetches_tickets_with_price_and_industry(self):
        class FakeDF:
            empty = False
            _data = [{"code": "600519", "market": 1, "price": 1800.0, "vol": 50000}]

            def iterrows(self):
                yield None, self._data[0]

            def __len__(self):
                return 1

        class FakeQ:
            def quotes(self, symbol):
                return FakeDF()

        industry_map = {"sh.600519": "白酒"}
        provider = MootDxUniverseProvider()
        tickets = provider._fetch_quotes_batch(FakeQ(), ["sh.600519"], industry_map)

        assert len(tickets) == 1
        assert tickets[0].ticker == "sh.600519"
        assert tickets[0].price == 1800.0
        assert tickets[0].industry == "白酒"
        assert tickets[0].volume == 50000.0

    def test_zero_price_skipped(self):
        class FakeDF:
            empty = False
            _data = [{"code": "600519", "market": 1, "price": 0.0}]

            def iterrows(self):
                yield None, self._data[0]

            def __len__(self):
                return 1

        class FakeQ:
            def quotes(self, symbol):
                return FakeDF()

        provider = MootDxUniverseProvider()
        tickets = provider._fetch_quotes_batch(FakeQ(), ["sh.600519"], {})
        assert tickets == []

    def test_empty_df_returns_empty(self):
        class FakeDF:
            empty = True

            def iterrows(self):
                return iter([])

            def __len__(self):
                return 0

        class FakeQ:
            def quotes(self, symbol):
                return FakeDF()

        provider = MootDxUniverseProvider()
        tickets = provider._fetch_quotes_batch(FakeQ(), ["sh.600519"], {})
        assert tickets == []


class TestMootDxPopulateMarketCaps:
    """Test _populate_market_caps helper."""

    def test_fills_market_cap_from_finance(self):
        class FakeFinanceDF:
            empty = False

            class _Series:
                values = [500000000]  # 500M shares

            def __getitem__(self, key):
                return self._Series()

        class FakeQ:
            def finance(self, symbol):
                return FakeFinanceDF()

        ticket = UniverseTicket(ticker="sh.600519", price=1800.0, source="mootdx")
        provider = MootDxUniverseProvider(populate_market_cap=True)
        result = provider._populate_market_caps(FakeQ(), [ticket])

        assert len(result) == 1
        # 500M shares × 1800元 / 1亿 = 9000亿元
        assert result[0].market_cap == 9000.0

    def test_skips_when_price_none(self):
        class FakeQ:
            def finance(self, symbol):
                class DF:
                    empty = False

                    class _Series:
                        values = [5000000000]

                    def __getitem__(self, key):
                        return self._Series()

                return DF()

        ticket = UniverseTicket(ticker="sh.600519", price=None, source="mootdx")
        provider = MootDxUniverseProvider(populate_market_cap=True)
        result = provider._populate_market_caps(FakeQ(), [ticket])

        assert result[0].market_cap is None

    def test_finance_exception_handled(self):
        class FakeQ:
            def finance(self, symbol):
                raise RuntimeError("finance api down")

        ticket = UniverseTicket(ticker="sh.600519", price=1800.0, source="mootdx")
        provider = MootDxUniverseProvider(populate_market_cap=True)
        result = provider._populate_market_caps(FakeQ(), [ticket])

        assert result[0].market_cap is None  # failed silently

    def test_empty_list(self):
        class FakeQ:
            pass

        provider = MootDxUniverseProvider(populate_market_cap=True)
        result = provider._populate_market_caps(FakeQ(), [])
        assert result == []


class TestMootDxFetchStockCodes:
    """Test _fetch_stock_codes helper."""

    def test_filters_non_a_share_and_delisted(self):
        class FakeRS:
            _rows = [
                ["sh.600519", "茅台", "2001-01-01", "", "1", "1"],  # A-share listed
                ["sh.900901", "B股", "2000-01-01", "", "2", "1"],  # B-share, skip
                ["sz.000000", "退市", "2020-01-01", "2025-01-01", "1", "0"],  # delisted, skip
                ["sz.300750", "宁德时代", "2018-06-01", "", "1", "1"],  # A-share listed
            ]
            _idx = 0

            def next(self):
                ok = self._idx < len(self._rows)
                if ok:
                    self._idx += 1
                return ok

            def get_row_data(self):
                return list(self._rows[self._idx - 1])

        rs = FakeRS()
        codes = MootDxUniverseProvider._fetch_stock_codes(rs)
        assert codes == ["sh.600519", "sz.300750"]

    def test_short_rows_skipped(self):
        class FakeRS:
            _rows = [["sh.600519", "茅台"], ["sz.000858", "五粮液", "2000-01-01", "", "1", "1"]]
            _idx = 0

            def next(self):
                ok = self._idx < len(self._rows)
                if ok:
                    self._idx += 1
                return ok

            def get_row_data(self):
                return list(self._rows[self._idx - 1])

        codes = MootDxUniverseProvider._fetch_stock_codes(FakeRS())
        assert codes == ["sz.000858"]

    def test_empty_result(self):
        class FakeRS:
            def next(self):
                return False

            def get_row_data(self):
                return []

        assert MootDxUniverseProvider._fetch_stock_codes(FakeRS()) == []


# ═════════════════════════════════════════════════════════════════════════════
#  TongHuaShunUniverseProvider
# ═════════════════════════════════════════════════════════════════════════════


class TestTongHuaShunUniverseProvider:
    """TongHuaShunUniverseProvider full path tests."""

    def _make_fake_requests(self):
        """Create a fake requests module with mocked responses."""
        fake_resp_list = []
        fake_resp_ticker_list = []

        class FakeResponse:
            def __init__(self, json_data, status_code=200):
                self._json = json_data
                self.status_code = status_code

            def json(self):
                return self._json

            def raise_for_status(self):
                if self.status_code >= 400:
                    raise RuntimeError(f"HTTP {self.status_code}")

        # Ticker list response
        ticker_items = [
            {"thscode": "600519.SH", "ticker": "贵州茅台"},
            {"thscode": "000858.SZ", "ticker": "五粮液"},
        ]
        fake_resp_ticker_list.append(FakeResponse({"code": 0, "data": {"item": ticker_items}}))

        # Snapshot response
        snapshot_items = [
            {"thscode": "600519.SH", "ticker": "贵州茅台", "last_price": 1800.0, "volume": 50000},
            {"thscode": "000858.SZ", "ticker": "五粮液", "last_price": 150.0, "volume": 120000},
        ]
        fake_resp_list.append(FakeResponse({"code": 0, "data": {"item": snapshot_items}}))

        class FakeRequests:
            def get(self, url, params=None, headers=None, timeout=30):
                # route by URL pattern
                if "tickers/list" in url:
                    return (
                        fake_resp_ticker_list.pop(0)
                        if fake_resp_ticker_list
                        else FakeResponse({"code": 1, "data": {"item": []}})
                    )
                if "prices/snapshot" in url:
                    return (
                        fake_resp_list.pop(0)
                        if fake_resp_list
                        else FakeResponse({"code": 1, "data": {"item": []}})
                    )
                return FakeResponse({"code": 1}, status_code=404)

        return FakeRequests()

    def test_get_universe_returns_tickets(self):
        fake_req = self._make_fake_requests()

        import trade_krono_cli.universe.provider as provider_mod

        original_requests = provider_mod.requests
        try:
            provider_mod.requests = fake_req
            with patch.dict("os.environ", {"HITHINK_FINANCE_API_KEY": "test-key"}):
                provider = TongHuaShunUniverseProvider()
                tickets = provider.get_universe()
        finally:
            provider_mod.requests = original_requests

        assert len(tickets) == 2
        by_ticker = {t.ticker: t for t in tickets}
        assert by_ticker["sh.600519"].name == "贵州茅台"
        assert by_ticker["sh.600519"].price == 1800.0
        assert by_ticker["sh.600519"].volume == 50000.0
        assert by_ticker["sh.600519"].source == "tonghuashun"

    def test_get_universe_no_api_key_returns_empty(self):
        with patch.dict("os.environ", {}, clear=True):
            provider = TongHuaShunUniverseProvider()
            assert provider.get_universe() == []

    def test_get_universe_import_error_returns_empty(self):
        with (
            patch.dict("os.environ", {"HITHINK_FINANCE_API_KEY": "test-key"}),
            patch.dict("sys.modules", {"requests": None}),
        ):
            provider = TongHuaShunUniverseProvider()
            assert provider.get_universe() == []

    def test_thscode_to_ticker_sh(self):
        assert TongHuaShunUniverseProvider._thscode_to_ticker("600519.SH") == "sh.600519"

    def test_thscode_to_ticker_sz(self):
        assert TongHuaShunUniverseProvider._thscode_to_ticker("000858.SZ") == "sz.000858"

    def test_thscode_to_ticker_bj(self):
        assert TongHuaShunUniverseProvider._thscode_to_ticker("830000.BJ") == "bj.830000"

    def test_thscode_to_ticker_missing_dot(self):
        assert TongHuaShunUniverseProvider._thscode_to_ticker("600519") == ""

    def test_thscode_to_ticker_unknown_exchange(self):
        assert TongHuaShunUniverseProvider._thscode_to_ticker("600519.HK") == ""

    def test_init_populate_market_cap_ignored(self):
        """populate_market_cap is accepted but ignored (tonghuashun doesn't support it)."""
        provider = TongHuaShunUniverseProvider(populate_market_cap=True)
        assert provider._populate_market_cap is True


# ═════════════════════════════════════════════════════════════════════════════
#  Factory: get_universe_provider
# ═════════════════════════════════════════════════════════════════════════════


class TestGetUniverseProvider:
    """Factory function get_universe_provider fallback behavior."""

    def test_known_source_akshare(self):
        provider = get_universe_provider("akshare")
        assert provider is not None
        assert provider.name == "akshare"

    def test_known_source_mootdx(self):
        provider = get_universe_provider("mootdx")
        assert provider is not None
        assert isinstance(provider, MootDxUniverseProvider)
        assert provider._populate_market_cap is False

    def test_known_source_mootdx_with_market_cap(self):
        provider = get_universe_provider("mootdx", populate_market_cap=True)
        assert provider is not None
        assert isinstance(provider, MootDxUniverseProvider)
        assert provider._populate_market_cap is True

    def test_known_source_tonghuashun(self):
        provider = get_universe_provider("tonghuashun")
        assert provider is not None
        assert isinstance(provider, TongHuaShunUniverseProvider)

    def test_unknown_source_falls_back_to_akshare(self):
        with patch("trade_krono_cli.universe.provider.logger") as mock_logger:
            provider = get_universe_provider("unknown_source_xyz")
        assert provider is not None
        assert provider.name == "akshare"
        mock_logger.warning.assert_called_once()

    def test_no_registered_providers_returns_none(self):
        """Edge case: if akshare registration is missing, returns None."""
        with patch.dict("trade_krono_cli.universe.provider._PROVIDER_REGISTRY", {}, clear=True):
            result = get_universe_provider("akshare")
        assert result is None
