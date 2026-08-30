"""
tests/test_tonghuashun_provider.py — 同花顺数据源单元测试。
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from trade_krono_cli.data_providers.base import KlineData, RealtimeQuote, StockMetadata
from trade_krono_cli.data_providers.tonghuashun_provider import TongHuaShunProvider

# ═══════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _reset_provider():
    """每个测试前重置类状态。"""
    TongHuaShunProvider._initialized = False
    TongHuaShunProvider._api_key = ""
    yield
    TongHuaShunProvider._initialized = False
    TongHuaShunProvider._api_key = ""


@pytest.fixture
def provider() -> TongHuaShunProvider:
    return TongHuaShunProvider()


@pytest.fixture
def api_key(monkeypatch: pytest.MonkeyPatch) -> str:
    key = "test-fuyao-api-key-12345"
    monkeypatch.setenv("HITHINK_FINANCE_API_KEY", key)
    return key


# ═══════════════════════════════════════════════════════
# 初始化 & 健康检查
# ═══════════════════════════════════════════════════════


def test_init_raises_without_api_key(provider: TongHuaShunProvider, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("HITHINK_FINANCE_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="HITHINK_FINANCE_API_KEY"):
        provider.fetch_kline("sh.600519", "2026-01-01", "2026-08-29")


def test_health_check_success(provider: TongHuaShunProvider, api_key: str):
    mock_response = {
        "code": 0,
        "message": "success",
        "data": {"timestamp": 1747584000000, "item": [{"date_ms": 1747584000000}]},
    }
    with patch("trade_krono_cli.data_providers.tonghuashun_provider.requests.get") as mock_get:
        mock_get.return_value.json.return_value = mock_response
        mock_get.return_value.raise_for_status.return_value = None
        assert provider.health_check() is True


def test_health_check_failure(provider: TongHuaShunProvider, api_key: str):
    with patch("trade_krono_cli.data_providers.tonghuashun_provider.requests.get") as mock_get:
        mock_get.return_value.json.return_value = {"code": 2001, "message": "unauthorized"}
        mock_get.return_value.raise_for_status.return_value = None
        assert provider.health_check() is False


def test_health_check_exception(provider: TongHuaShunProvider, api_key: str):
    with patch("trade_krono_cli.data_providers.tonghuashun_provider.requests.get") as mock_get:
        mock_get.side_effect = Exception("network error")
        assert provider.health_check() is False


# ═══════════════════════════════════════════════════════
# fetch_kline
# ═══════════════════════════════════════════════════════


def test_fetch_kline_success(provider: TongHuaShunProvider, api_key: str):
    mock_response = {
        "code": 0,
        "message": "success",
        "data": {
            "timestamp": 1747584000000,
            "item": [
                {
                    "date_ms": 1747584000000,
                    "open_price": 1600.0,
                    "high_price": 1620.0,
                    "low_price": 1590.0,
                    "close_price": 1610.0,
                    "volume": 3000000,
                    "turnover": 5000000000.0,
                }
            ],
        },
    }
    with patch("trade_krono_cli.data_providers.tonghuashun_provider.requests.get") as mock_get:
        mock_get.return_value.json.return_value = mock_response
        mock_get.return_value.raise_for_status.return_value = None
        result = provider.fetch_kline("sh.600519", "2026-08-01", "2026-08-29")

    assert result is not None
    assert isinstance(result, KlineData)
    assert result.length == 1
    assert result.close[0] == 1610.0
    assert result.volume[0] == 3000000.0
    assert result.amount[0] == 5000000000.0


def test_fetch_kline_empty_response(provider: TongHuaShunProvider, api_key: str):
    mock_response = {"code": 0, "message": "success", "data": {"item": []}}
    with patch("trade_krono_cli.data_providers.tonghuashun_provider.requests.get") as mock_get:
        mock_get.return_value.json.return_value = mock_response
        mock_get.return_value.raise_for_status.return_value = None
        result = provider.fetch_kline("sh.600519", "2026-08-01", "2026-08-29")
    assert result is None


def test_fetch_kline_api_error(provider: TongHuaShunProvider, api_key: str):
    mock_response = {"code": 3001, "message": "not found"}
    with patch("trade_krono_cli.data_providers.tonghuashun_provider.requests.get") as mock_get:
        mock_get.return_value.json.return_value = mock_response
        mock_get.return_value.raise_for_status.return_value = None
        result = provider.fetch_kline("sh.600519", "2026-08-01", "2026-08-29")
    assert result is None


def test_fetch_kline_http_error(provider: TongHuaShunProvider, api_key: str):
    import requests

    with patch("trade_krono_cli.data_providers.tonghuashun_provider.requests.get") as mock_get:
        mock_get.side_effect = requests.exceptions.HTTPError("404")
        result = provider.fetch_kline("sh.600519", "2026-08-01", "2026-08-29")
    assert result is None


# ═══════════════════════════════════════════════════════
# fetch_quote
# ═══════════════════════════════════════════════════════


def test_fetch_quote_success(provider: TongHuaShunProvider, api_key: str):
    mock_response = {
        "code": 0,
        "message": "success",
        "data": {
            "timestamp": 1747584000000,
            "total": 1,
            "item": [
                {
                    "thscode": "600519.SH",
                    "ticker": "600519",
                    "last_price": 1277.8,
                    "volume": 3098875,
                    "turnover": 3937375200,
                }
            ],
        },
    }
    with patch("trade_krono_cli.data_providers.tonghuashun_provider.requests.get") as mock_get:
        mock_get.return_value.json.return_value = mock_response
        mock_get.return_value.raise_for_status.return_value = None
        result = provider.fetch_quote("sh.600519")

    assert result is not None
    assert isinstance(result, RealtimeQuote)
    assert result.price == 1277.8
    assert result.source == "tonghuashun"


def test_fetch_quote_empty(provider: TongHuaShunProvider, api_key: str):
    mock_response = {"code": 0, "message": "success", "data": {"item": []}}
    with patch("trade_krono_cli.data_providers.tonghuashun_provider.requests.get") as mock_get:
        mock_get.return_value.json.return_value = mock_response
        mock_get.return_value.raise_for_status.return_value = None
        result = provider.fetch_quote("sh.600519")
    assert result is None


# ═══════════════════════════════════════════════════════
# fetch_metadata
# ═══════════════════════════════════════════════════════


def test_fetch_metadata_success(provider: TongHuaShunProvider, api_key: str):
    mock_response = {
        "code": 0,
        "message": "success",
        "data": {
            "timestamp": 1747584000000,
            "item": [
                {
                    "thscode": "600519.SH",
                    "ticker": "600519",
                    "name": "贵州茅台",
                    "exchange": "SH",
                    "asset_type": "a-share",
                    "currency": "CNY",
                }
            ],
        },
    }
    with patch("trade_krono_cli.data_providers.tonghuashun_provider.requests.get") as mock_get:
        mock_get.return_value.json.return_value = mock_response
        mock_get.return_value.raise_for_status.return_value = None
        result = provider.fetch_metadata("sh.600519")

    assert result is not None
    assert isinstance(result, StockMetadata)
    assert result.ticker == "sh.600519"
    assert result.source == "tonghuashun"


# ═══════════════════════════════════════════════════════
# 内部工具方法
# ═══════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "ticker,expected",
    [
        ("sh.600519", "600519.SH"),
        ("sz.000858", "000858.SZ"),
        ("bj.826521", "826521.BJ"),
        ("invalid", None),
        ("", None),
    ],
)
def test_ticker_to_thscode(ticker: str, expected: str | None):
    assert TongHuaShunProvider._ticker_to_thscode(ticker) == expected


@pytest.mark.parametrize(
    "thscode,expected",
    [
        ("600519.SH", "sh.600519"),
        ("000858.SZ", "sz.000858"),
        ("826521.BJ", "bj.826521"),
        ("invalid", ""),
    ],
)
def test_thscode_to_ticker(thscode: str, expected: str):
    assert TongHuaShunProvider._thscode_to_ticker(thscode) == expected


def test_date_to_ms():
    ms = TongHuaShunProvider._date_to_ms("2026-08-29")
    assert ms > 0
    assert isinstance(ms, int)


def test_safe_float_none():
    assert TongHuaShunProvider._safe_float(None) is None


def test_safe_float_valid():
    assert TongHuaShunProvider._safe_float(123.45) == 123.45


def test_safe_float_invalid():
    assert TongHuaShunProvider._safe_float("abc") is None
    assert TongHuaShunProvider._safe_float(float("nan")) is None
