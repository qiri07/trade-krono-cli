"""tests for scripts._utils — alias test to ensure 100% coverage."""
from __future__ import annotations

from scripts._utils import get_all_tickers, get_provider_chain


def test_get_all_tickers() -> None:
    tickers = get_all_tickers()
    assert isinstance(tickers, list)
    assert len(tickers) > 0


def test_get_provider_chain() -> None:
    assert get_provider_chain("sh.600519") == ["tonghuashun", "baostock", "mootdx"]
    assert get_provider_chain("bj.920001") == ["tonghuashun", "baostock"]
