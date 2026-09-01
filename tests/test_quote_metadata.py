"""测试 RealtimeQuote 和 StockMetadata 数据模型。"""

from __future__ import annotations

from trade_krono_cli.data_providers.base import RealtimeQuote, StockMetadata


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
