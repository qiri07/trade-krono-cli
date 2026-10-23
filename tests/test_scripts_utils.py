"""tests for scripts._utils shared utilities."""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest

from scripts._utils import get_all_tickers, get_provider_chain, normalize_ticker_key


class TestGetProviderChain:
    """Provider chain logic tests."""

    def test_sh_ticker(self) -> None:
        assert get_provider_chain("sh.600519") == ["tonghuashun", "baostock", "mootdx"]

    def test_sz_ticker(self) -> None:
        assert get_provider_chain("sz.000001") == ["tonghuashun", "baostock", "mootdx"]

    def test_sh_etf_ticker(self) -> None:
        """沪市 ETF（51/58 开头）baostock 优先。"""
        assert get_provider_chain("sh.510300") == ["baostock", "tonghuashun", "mootdx"]
        assert get_provider_chain("sh.588000") == ["baostock", "tonghuashun", "mootdx"]

    def test_sz_etf_ticker(self) -> None:
        """深市 ETF（159/156/158 开头）baostock 优先。"""
        assert get_provider_chain("sz.159915") == ["baostock", "tonghuashun", "mootdx"]
        assert get_provider_chain("sz.156001") == ["baostock", "tonghuashun", "mootdx"]

    def test_bj_ticker(self) -> None:
        assert get_provider_chain("bj.920001") == ["tonghuashun", "baostock"]

    def test_unknown_prefix(self) -> None:
        assert get_provider_chain("unknown.123456") == ["baostock", "tonghuashun"]

    def test_empty_ticker(self) -> None:
        assert get_provider_chain("") == ["baostock", "tonghuashun"]


class TestNormalizeTickerKey:
    """normalize_ticker_key 归一化测试。"""

    def test_sh_etf_code(self) -> None:
        assert normalize_ticker_key("510300") == "sh.510300"
        assert normalize_ticker_key("588000") == "sh.588000"

    def test_sz_etf_code(self) -> None:
        assert normalize_ticker_key("159915") == "sz.159915"
        assert normalize_ticker_key("156001") == "sz.156001"

    def test_a_share_code(self) -> None:
        assert normalize_ticker_key("600519") == "sh.600519"
        assert normalize_ticker_key("000858") == "sz.000858"
        assert normalize_ticker_key("920001") == "bj.920001"

    def test_prefixed_passthrough(self) -> None:
        assert normalize_ticker_key("sh.600519") == "sh.600519"
        assert normalize_ticker_key("hk.00700") == "hk.00700"


class TestGetAllTickers:
    """Ticker list retrieval tests."""

    def test_returns_sorted_list(self) -> None:
        with patch("scripts._utils.sqlite3.connect") as mock_connect:
            mock_conn = mock_connect.return_value
            mock_conn.execute.return_value.fetchall.return_value = [
                ("sh.600519",),
                ("sz.000001",),
            ]
            result = get_all_tickers()
            assert isinstance(result, list)
            assert result == sorted(result), "Result must be sorted"

    def test_non_empty(self) -> None:
        with patch("scripts._utils.sqlite3.connect") as mock_connect:
            mock_conn = mock_connect.return_value
            mock_conn.execute.return_value.fetchall.return_value = [
                ("sh.600519",),
                ("sz.000001",),
            ]
            result = get_all_tickers()
            assert len(result) > 0, "Should return at least some tickers"

    def test_all_have_dot(self) -> None:
        with patch("scripts._utils.sqlite3.connect") as mock_connect:
            mock_conn = mock_connect.return_value
            mock_conn.execute.return_value.fetchall.return_value = [
                ("sh.600519",),
                ("sz.000001",),
            ]
            result = get_all_tickers()
            assert all("." in t for t in result), "All tickers should have exchange prefix"

    def test_db_not_found_raises(self) -> None:
        """When DB is missing, should raise an error."""
        with patch("scripts._utils.sqlite3.connect") as mock_connect:
            mock_connect.side_effect = sqlite3.OperationalError("no such file")
            with pytest.raises(sqlite3.OperationalError):
                get_all_tickers()
