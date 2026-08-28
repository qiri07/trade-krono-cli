"""
Tests for MootDxUniverseProvider — covers uncovered lines 142-143, 161-235.
"""

from __future__ import annotations

from types import ModuleType
from unittest.mock import patch

from trade_krono_cli.universe.provider import MootDxUniverseProvider


def _make_fake_bs(login_ok=True, codes=None):
    """Create a fake baostock module for mocking."""
    fake_bs = ModuleType("baostock")

    class FakeLoginResult:
        def __init__(self, ok):
            self.error_code = "0" if ok else "1"
            self.error_msg = "success" if ok else "login failed"

    class FakeRecordSet:
        def __init__(self, rows):
            self._rows = rows
            self._idx = 0

        def next(self):
            return self._idx < len(self._rows)

        def get_row_data(self):
            if self._idx < len(self._rows):
                row = list(self._rows[self._idx])
                self._idx += 1
                return row
            return []

    def fake_login():
        return FakeLoginResult(login_ok)

    def fake_logout():
        pass

    rows = codes if codes is not None else [
        ("sh.600519", "贵州茅台", "2001-08-27", "", "1", "1"),
        ("sz.000858", "五粮液", "2000-12-18", "", "1", "1"),
        ("sh.601318", "中国平安", "2007-01-09", "", "1", "1"),
        ("sh.900901", "弃牌B股", "2000-01-01", "", "2", "1"),
        ("sz.000000", "退市股", "2020-01-01", "2025-01-01", "1", "0"),
    ]
    def fake_query():
        return FakeRecordSet(rows)

    # Industry lookup: maps ticker → (industry_code, industry_name)
    _industry_data: dict[str, list[list]] = {
        "sh.600519": [["B61", "银行"]],
        "sz.000858": [["C21", "食品饮料"]],
        "sh.601318": [["B61", "银行"]],
    }

    class FakeIndustryRecordSet:
        def __init__(self, rows):
            self._rows = rows
            self._idx = 0
            self.error_code = "0"

        def next(self):
            return self._idx < len(self._rows)

        def get_row_data(self):
            if self._idx < len(self._rows):
                row = list(self._rows[self._idx])
                self._idx += 1
                return row
            return []

    def fake_query_industry(code=""):
        rows = _industry_data.get(str(code), [])
        return FakeIndustryRecordSet(rows)

    fake_bs.login = fake_login
    fake_bs.logout = fake_logout
    fake_bs.query_stock_basic = fake_query
    fake_bs.query_stock_industry = fake_query_industry
    return fake_bs


class TestMootDxBatchFetch:
    """Test successful batch fetching via mootdx."""

    def test_single_batch_returns_tickets(self):
        """One batch of codes, all succeed → tickets returned."""
        fake_bs = _make_fake_bs()

        class FakeDF:
            empty = False
            _data = [
                {"code": "600519", "market": 1, "price": 1800.0},
                {"code": "000858", "market": 0, "price": 150.0},
            ]
            def iterrows(self):
                for row in self._data:
                    yield None, row
            def __len__(self):
                return len(self._data)

        class FakeQuotes:
            def quotes(self, symbol):
                return FakeDF()

        # Create the mootdx module structure
        fake_quotes_mod = ModuleType("mootdx.quotes")
        fake_quotes_mod.Quotes = FakeQuotes

        fake_mootdx = ModuleType("mootdx")
        fake_mootdx.quotes = fake_quotes_mod
        # Need Quotes class with factory method
        class QuotesClass:
            @staticmethod
            def factory(market):
                return FakeQuotes()
        fake_quotes_mod.Quotes = QuotesClass

        with patch.dict("sys.modules", {"baostock": fake_bs, "mootdx": fake_mootdx, "mootdx.quotes": fake_quotes_mod}):
            provider = MootDxUniverseProvider()
            result = provider.get_universe()
            assert len(result) == 2
            tickers = {t.ticker for t in result}
            assert "sh.600519" in tickers
            assert "sz.000858" in tickers

    def test_batch_with_zero_price_skipped(self):
        """Rows with price <= 0 are skipped."""
        fake_bs = _make_fake_bs()

        class FakeDF:
            empty = False
            _data = [
                {"code": "600519", "market": 1, "price": 0.0},
                {"code": "000858", "market": 0, "price": 150.0},
            ]
            def iterrows(self):
                for row in self._data:
                    yield None, row
            def __len__(self):
                return len(self._data)

        class FakeQuotes:
            def quotes(self, symbol):
                return FakeDF()

        class QuotesClass:
            @staticmethod
            def factory(market):
                return FakeQuotes()

        fake_quotes_mod = ModuleType("mootdx.quotes")
        fake_quotes_mod.Quotes = QuotesClass
        fake_mootdx = ModuleType("mootdx")
        fake_mootdx.quotes = fake_quotes_mod

        with patch.dict("sys.modules", {"baostock": fake_bs, "mootdx": fake_mootdx, "mootdx.quotes": fake_quotes_mod}):
            provider = MootDxUniverseProvider()
            result = provider.get_universe()
            assert len(result) == 1
            assert result[0].ticker == "sz.000858"

    def test_batch_partial_failure_continues(self):
        """One batch fails, others succeed → partial results returned."""
        # Provide 25 codes so they span 2 batches (batch_size=20)
        extra_codes = [(f"sh.{601000+i}", f"股票{i}", "2020-01-01", "", "1", "1") for i in range(23)]
        fake_bs = _make_fake_bs(codes=[
            ("sh.600519", "贵州茅台", "2001-08-27", "", "1", "1"),
            ("sz.000858", "五粮液", "2000-12-18", "", "1", "1"),
        ] + extra_codes)

        call_count = [0]

        class FakeDF:
            empty = False
            _data = [{"code": "601318", "market": 1, "price": 50.0}]
            def iterrows(self):
                for row in self._data:
                    yield None, row
            def __len__(self):
                return len(self._data)

        class FakeQuotes:
            def quotes(self, symbol):
                call_count[0] += 1
                if call_count[0] == 1:
                    raise RuntimeError("batch timeout")
                return FakeDF()

        class QuotesClass:
            @staticmethod
            def factory(market):
                return FakeQuotes()

        fake_quotes_mod = ModuleType("mootdx.quotes")
        fake_quotes_mod.Quotes = QuotesClass
        fake_mootdx = ModuleType("mootdx")
        fake_mootdx.quotes = fake_quotes_mod

        with patch.dict("sys.modules", {"baostock": fake_bs, "mootdx": fake_mootdx, "mootdx.quotes": fake_quotes_mod}):
            provider = MootDxUniverseProvider()
            result = provider.get_universe()
            assert len(result) >= 1
            tickers = {t.ticker for t in result}
            assert "sh.601318" in tickers

    def test_empty_df_from_mootdx(self):
        """mootdx returns empty DataFrame → no tickets from that batch."""
        fake_bs = _make_fake_bs()

        class FakeDF:
            empty = True
            def iterrows(self):
                return iter([])
            def __len__(self):
                return 0

        class FakeQuotes:
            def quotes(self, symbol):
                return FakeDF()

        class QuotesClass:
            @staticmethod
            def factory(market):
                return FakeQuotes()

        fake_quotes_mod = ModuleType("mootdx.quotes")
        fake_quotes_mod.Quotes = QuotesClass
        fake_mootdx = ModuleType("mootdx")
        fake_mootdx.quotes = fake_quotes_mod

        with patch.dict("sys.modules", {"baostock": fake_bs, "mootdx": fake_mootdx, "mootdx.quotes": fake_quotes_mod}):
            provider = MootDxUniverseProvider()
            result = provider.get_universe()
            assert result == []

    def test_health_check(self):
        """health_check returns True when get_universe returns tickets."""
        fake_bs = _make_fake_bs()

        class FakeDF:
            empty = False
            _data = [{"code": "600519", "market": 1, "price": 1800.0}]
            def iterrows(self):
                for row in self._data:
                    yield None, row
            def __len__(self):
                return len(self._data)

        class FakeQuotes:
            def quotes(self, symbol):
                return FakeDF()

        class QuotesClass:
            @staticmethod
            def factory(market):
                return FakeQuotes()

        fake_quotes_mod = ModuleType("mootdx.quotes")
        fake_quotes_mod.Quotes = QuotesClass
        fake_mootdx = ModuleType("mootdx")
        fake_mootdx.quotes = fake_quotes_mod

        with patch.dict("sys.modules", {"baostock": fake_bs, "mootdx": fake_mootdx, "mootdx.quotes": fake_quotes_mod}):
            provider = MootDxUniverseProvider()
            assert provider.health_check() is True

    def test_health_check_false_on_empty(self):
        """health_check returns False when get_universe returns empty."""
        fake_bs = _make_fake_bs()

        class FakeDF:
            empty = True
            def iterrows(self):
                return iter([])
            def __len__(self):
                return 0

        class FakeQuotes:
            def quotes(self, symbol):
                return FakeDF()

        class QuotesClass:
            @staticmethod
            def factory(market):
                return FakeQuotes()

        fake_quotes_mod = ModuleType("mootdx.quotes")
        fake_quotes_mod.Quotes = QuotesClass
        fake_mootdx = ModuleType("mootdx")
        fake_mootdx.quotes = fake_quotes_mod

        with patch.dict("sys.modules", {"baostock": fake_bs, "mootdx": fake_mootdx, "mootdx.quotes": fake_quotes_mod}):
            provider = MootDxUniverseProvider()
            assert provider.health_check() is False

    def test_baostock_exception_returns_empty(self):
        """baostock raises exception → returns [] after logout guard."""
        fake_bs = ModuleType("baostock")

        def bad_login():
            raise RuntimeError("baostock socket error")

        fake_bs.login = bad_login

        with patch.dict("sys.modules", {"baostock": fake_bs}):
            provider = MootDxUniverseProvider()
            result = provider.get_universe()
            assert result == []

    def test_non_a_share_filtered_out(self):
        """stock_type != '1' rows are excluded from raw_codes."""
        fake_bs = _make_fake_bs(codes=[
            ("sh.900901", "B股", "2000-01-01", "", "2", "1"),
            ("sz.400001", "新三板", "2010-01-01", "", "3", "1"),
        ])

        class FakeDF:
            empty = True
            def iterrows(self):
                return iter([])
            def __len__(self):
                return 0

        class FakeQuotes:
            def quotes(self, symbol):
                return FakeDF()

        class QuotesClass:
            @staticmethod
            def factory(market):
                return FakeQuotes()

        fake_quotes_mod = ModuleType("mootdx.quotes")
        fake_quotes_mod.Quotes = QuotesClass
        fake_mootdx = ModuleType("mootdx")
        fake_mootdx.quotes = fake_quotes_mod

        with patch.dict("sys.modules", {"baostock": fake_bs, "mootdx": fake_mootdx, "mootdx.quotes": fake_quotes_mod}):
            provider = MootDxUniverseProvider()
            result = provider.get_universe()
            assert result == []

    def test_delisted_stock_filtered_out(self):
        """status != '1' (delisted) rows are excluded from raw_codes."""
        fake_bs = _make_fake_bs(codes=[
            ("sz.000000", "退市股", "2020-01-01", "2025-01-01", "1", "0"),
        ])

        class FakeDF:
            empty = True
            def iterrows(self):
                return iter([])
            def __len__(self):
                return 0

        class FakeQuotes:
            def quotes(self, symbol):
                return FakeDF()

        class QuotesClass:
            @staticmethod
            def factory(market):
                return FakeQuotes()

        fake_quotes_mod = ModuleType("mootdx.quotes")
        fake_quotes_mod.Quotes = QuotesClass
        fake_mootdx = ModuleType("mootdx")
        fake_mootdx.quotes = fake_quotes_mod

        with patch.dict("sys.modules", {"baostock": fake_bs, "mootdx": fake_mootdx, "mootdx.quotes": fake_quotes_mod}):
            provider = MootDxUniverseProvider()
            result = provider.get_universe()
            assert result == []


class TestMootDxBaostockLoginFailure:
    def test_login_failure_returns_empty(self):
        """baostock login fails → return [] immediately."""
        fake_bs = _make_fake_bs(login_ok=False)

        with patch.dict("sys.modules", {"baostock": fake_bs}):
            provider = MootDxUniverseProvider()
            result = provider.get_universe()
            assert result == []


class TestMootDxMootdxInitFailure:
    def test_mootdx_init_raises(self):
        """mootdx Quotes.factory raises → returns [] after baostock logout."""
        fake_bs = _make_fake_bs()

        class BadQuotes:
            @staticmethod
            def factory(market):
                raise RuntimeError("mootdx connection refused")

        fake_quotes_mod = ModuleType("mootdx.quotes")
        fake_quotes_mod.Quotes = BadQuotes
        fake_mootdx = ModuleType("mootdx")
        fake_mootdx.quotes = fake_quotes_mod

        with patch.dict("sys.modules", {"baostock": fake_bs, "mootdx": fake_mootdx, "mootdx.quotes": fake_quotes_mod}):
            provider = MootDxUniverseProvider()
            result = provider.get_universe()
            assert result == []


class TestMootDxNoCodes:
    def test_empty_code_list_returns_empty(self):
        """No A-share codes → mootdx never called, returns []."""
        fake_bs = _make_fake_bs(codes=[])

        class FakeDF:
            empty = True
            def iterrows(self):
                return iter([])
            def __len__(self):
                return 0

        class FakeQuotes:
            def quotes(self, symbol):
                return FakeDF()

        class QuotesClass:
            @staticmethod
            def factory(market):
                return FakeQuotes()

        fake_quotes_mod = ModuleType("mootdx.quotes")
        fake_quotes_mod.Quotes = QuotesClass
        fake_mootdx = ModuleType("mootdx")
        fake_mootdx.quotes = fake_quotes_mod

        with patch.dict("sys.modules", {"baostock": fake_bs, "mootdx": fake_mootdx, "mootdx.quotes": fake_quotes_mod}):
            provider = MootDxUniverseProvider()
            result = provider.get_universe()
            assert result == []


class TestFillIndustry:
    """Tests for MootDxUniverseProvider._fill_industry."""

    def test_fill_industry_populates_known_tickers(self):
        """Known tickers get their industry from baostock."""
        from trade_krono_cli.universe.provider import UniverseTicket

        tickets = [
            UniverseTicket(ticker="sh.600519", price=1800.0),
            UniverseTicket(ticker="sz.000858", price=150.0),
            UniverseTicket(ticker="sh.999999", price=50.0),  # unknown
        ]
        fake_bs = _make_fake_bs()

        with patch.dict("sys.modules", {"baostock": fake_bs}):
            provider = MootDxUniverseProvider()
            provider._fill_industry(tickets)

        assert tickets[0].industry == "银行"
        assert tickets[1].industry == "食品饮料"
        assert tickets[2].industry is None  # not in lookup

    def test_fill_industry_baostock_import_error(self):
        """ImportError is silently ignored."""
        from trade_krono_cli.universe.provider import UniverseTicket

        tickets = [UniverseTicket(ticker="sh.600519")]
        fake_bs_no_import = ModuleType("baostock")

        with patch.dict("sys.modules", {"baostock": fake_bs_no_import}):
            provider = MootDxUniverseProvider()
            provider._fill_industry(tickets)

        assert tickets[0].industry is None

    def test_fill_industry_login_failure(self):
        """baostock login failure is silently ignored."""
        from trade_krono_cli.universe.provider import UniverseTicket

        tickets = [UniverseTicket(ticker="sh.600519")]
        fake_bs = _make_fake_bs(login_ok=False)

        with patch.dict("sys.modules", {"baostock": fake_bs}):
            provider = MootDxUniverseProvider()
            provider._fill_industry(tickets)

        assert tickets[0].industry is None


class TestMootDxIndustryInGetUniverse:
    """Test that get_universe populates industry for mootdx provider."""

    def test_industry_populated_from_baostock(self):
        """Tickets returned by get_universe should have industry filled."""
        fake_bs = _make_fake_bs()

        class FakeDF:
            empty = False
            _data = [{"code": "600519", "market": 1, "price": 1800.0}]
            def iterrows(self):
                for row in self._data:
                    yield None, row
            def __len__(self):
                return len(self._data)

        class FakeQuotes:
            def quotes(self, symbol):
                return FakeDF()

        class QuotesClass:
            @staticmethod
            def factory(market):
                return FakeQuotes()

        fake_quotes_mod = ModuleType("mootdx.quotes")
        fake_quotes_mod.Quotes = QuotesClass
        fake_mootdx = ModuleType("mootdx")
        fake_mootdx.quotes = fake_quotes_mod

        with patch.dict("sys.modules", {"baostock": fake_bs, "mootdx": fake_mootdx, "mootdx.quotes": fake_quotes_mod}):
            provider = MootDxUniverseProvider()
            result = provider.get_universe()
            assert len(result) == 1
            assert result[0].ticker == "sh.600519"
            assert result[0].industry == "银行"
