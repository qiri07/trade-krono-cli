"""
Tests for fetch_stock_meta in stock_filter.py — covers lines 386-449.

Uses sys.modules patching to mock the baostock import inside fetch_stock_meta.
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from types import ModuleType
from unittest.mock import patch

from trade_krono_cli.stock_filter import StockMeta, fetch_stock_meta


def _make_fake_bs_module(login_ok=True, performance_rows=None, industry_rows=None, basic_rows=None):
    """Create a complete fake baostock module."""
    fake_bs = ModuleType("baostock")

    class LoginResult:
        def __init__(self, ok):
            self.error_code = "0" if ok else "1"
            self.error_msg = "success" if ok else "login failed"

    def fake_login():
        return LoginResult(login_ok)

    def fake_logout():
        pass

    class FakeRecordSet:
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

    def fake_query_stock_industry(code):
        return FakeRecordSet(industry_rows or [])

    def fake_query_stock_performance(code):
        return FakeRecordSet(performance_rows or [])

    def fake_query_stock_basic(code):
        return FakeRecordSet(basic_rows or [])

    fake_bs.login = fake_login
    fake_bs.logout = fake_logout
    fake_bs.query_stock_industry = fake_query_stock_industry
    fake_bs.query_stock_performance = fake_query_stock_performance
    fake_bs.query_stock_basic = fake_query_stock_basic
    return fake_bs


@contextmanager
def _swap_baostock(fake_bs):
    """Swap baostock in sys.modules and restore afterward."""
    real_bs = sys.modules.pop("baostock", None)
    try:
        with patch.dict("sys.modules", {"baostock": fake_bs}):
            yield
    finally:
        if real_bs is not None:
            sys.modules["baostock"] = real_bs


class TestFetchStockMetaImportError:
    """Test when baostock is not installed."""

    def test_import_error_returns_stubs(self):
        """baostock not installed → return stub StockMeta for each ticker."""
        # Since baostock is installed in this environment, we test the ImportError
        # path by patching the module object to raise AttributeError on access.
        # This simulates what would happen if baostock were unavailable.
        # We use a more direct approach: monkeypatch fetch_stock_meta's inner import.
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "baostock" or name.startswith("baostock."):
                raise ImportError(f"No module named '{name}'")
            return real_import(name, *args, **kwargs)

        builtins.__import__ = fake_import
        try:
            # Clear any cached baostock modules
            mods_to_remove = [k for k in list(sys.modules.keys()) if k.startswith("baostock")]
            for m in mods_to_remove:
                sys.modules.pop(m, None)

            result = fetch_stock_meta(["sh.600519", "sz.000858"], "2026-08-11")
            assert len(result) == 2
            assert "sh.600519" in result
            assert "sz.000858" in result
            assert isinstance(result["sh.600519"], StockMeta)
            assert result["sh.600519"].industry is None
            assert result["sh.600519"].pe_ttm is None
        finally:
            builtins.__import__ = real_import


class TestFetchStockMetaLoginFailure:
    """Test when baostock login fails."""

    def test_login_failure_returns_stubs(self):
        """baostock login fails → return stub StockMeta for each ticker."""
        fake_bs = _make_fake_bs_module(login_ok=False)
        with _swap_baostock(fake_bs):
            result = fetch_stock_meta(["sh.600519"], "2026-08-11")
            assert len(result) == 1
            assert result["sh.600519"].ticker == "sh.600519"
            assert result["sh.600519"].industry is None
            assert result["sh.600519"].pe_ttm is None


class TestFetchStockMetaSuccess:
    """Test successful metadata fetching."""

    def test_full_metadata_fetch(self):
        """All queries succeed → StockMeta populated correctly."""
        performance_rows = [
            ["sh.600519", "2025-12-31", "28.5", "3.2", "0.85", "0.12"],
        ]
        industry_rows = [
            ["B00801", "银行"],
        ]
        basic_rows = [
            ["sh.600519", "贵州茅台", "2001-08-27", "", "1", "1"],
        ]
        fake_bs = _make_fake_bs_module(
            login_ok=True,
            performance_rows=performance_rows,
            industry_rows=industry_rows,
            basic_rows=basic_rows,
        )

        with _swap_baostock(fake_bs):
            result = fetch_stock_meta(["sh.600519"], "2026-08-11")
            meta = result["sh.600519"]
            assert meta.ticker == "sh.600519"
            assert meta.industry == "银行"
            assert meta.industry_code == "B00801"
            assert meta.pe_ttm == 28.5
            assert meta.pb == 3.2

    def test_empty_performance_returns_stub_fields(self):
        """Empty performance query → pe_ttm/pb remain None."""
        fake_bs = _make_fake_bs_module(login_ok=True, performance_rows=[])

        with _swap_baostock(fake_bs):
            result = fetch_stock_meta(["sh.600519"], "2026-08-11")
            meta = result["sh.600519"]
            assert meta.pe_ttm is None
            assert meta.pb is None

    def test_performance_short_row_skipped(self):
        """Performance row with < 3 fields → pe_ttm not set."""
        fake_bs = _make_fake_bs_module(
            login_ok=True,
            performance_rows=[["sh.600519", "2025-12-31"]],
        )

        with _swap_baostock(fake_bs):
            result = fetch_stock_meta(["sh.600519"], "2026-08-11")
            meta = result["sh.600519"]
            assert meta.pe_ttm is None

    def test_invalid_pe_value_skipped(self):
        """Non-float pe_ttm value → skipped gracefully."""
        fake_bs = _make_fake_bs_module(
            login_ok=True,
            performance_rows=[["sh.600519", "2025-12-31", "N/A", "3.2"]],
        )

        with _swap_baostock(fake_bs):
            result = fetch_stock_meta(["sh.600519"], "2026-08-11")
            meta = result["sh.600519"]
            assert meta.pe_ttm is None
            assert meta.pb == 3.2

    def test_multiple_tickers(self):
        """Multiple tickers → all fetched independently."""
        perf_rows_1 = [["sh.600519", "2025-12-31", "28.5", "3.2"]]
        perf_rows_2 = [["sz.000858", "2025-12-31", "22.0", "4.1"]]

        def make_perf_query(code):
            rows = perf_rows_1 if code == "sh.600519" else perf_rows_2

            class FakeRS:
                def __init__(r, data):
                    r._rows = data
                    r._idx = 0
                    r.error_code = "0"

                def next(r):
                    return r._idx < len(r._rows)

                def get_row_data(r):
                    if r._idx < len(r._rows):
                        row = list(r._rows[r._idx])
                        r._idx += 1
                        return row
                    return []

            return FakeRS(rows)

        fake_bs = _make_fake_bs_module(login_ok=True)
        fake_bs.query_stock_performance = make_perf_query

        with _swap_baostock(fake_bs):
            result = fetch_stock_meta(["sh.600519", "sz.000858"], "2026-08-11")
            assert len(result) == 2
            assert result["sh.600519"].pe_ttm == 28.5
            assert result["sz.000858"].pe_ttm == 22.0

    def test_industry_empty_row(self):
        """Industry query returns empty row → industry stays None."""
        fake_bs = _make_fake_bs_module(login_ok=True, industry_rows=[])

        with _swap_baostock(fake_bs):
            result = fetch_stock_meta(["sh.600519"], "2026-08-11")
            assert result["sh.600519"].industry is None

    def test_industry_short_row(self):
        """Industry row with < 2 fields → industry_code set but industry None."""
        fake_bs = _make_fake_bs_module(
            login_ok=True,
            industry_rows=[["B00801"]],
        )

        with _swap_baostock(fake_bs):
            result = fetch_stock_meta(["sh.600519"], "2026-08-11")
            meta = result["sh.600519"]
            assert meta.industry is None
            assert meta.industry_code == "B00801"

    def test_basic_query_no_op_for_market_cap(self):
        """query_stock_basic results don't affect any fields (all remain None)."""
        fake_bs = _make_fake_bs_module(
            login_ok=True,
            basic_rows=[["sh.600519", "贵州茅台", "2001-08-27", "", "1", "1"]],
        )

        with _swap_baostock(fake_bs):
            result = fetch_stock_meta(["sh.600519"], "2026-08-11")
            meta = result["sh.600519"]
            assert meta.industry is None
            assert meta.pe_ttm is None
            assert meta.pb is None
