"""测试 DataProvider ABC 接口约束。"""

from __future__ import annotations

import pytest

from trade_krono_cli.data_providers.base import (
    DataProvider,
    KlineData,
    RealtimeQuote,
    StockMetadata,
)


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


