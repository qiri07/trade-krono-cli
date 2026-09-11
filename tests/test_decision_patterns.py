"""Tests for ta_decision/patterns.py — regex pattern matching."""

from __future__ import annotations

from trade_krono_cli.ta_decision.patterns import (
    RE_CATALYSTS,
    RE_ENTRY_ZONE,
    RE_HOLDING_PERIOD,
    RE_INVALIDATIONS,
    RE_PCT,
    RE_POS_SIZE,
    RE_RATING,
    RE_STOP_LOSS,
    RE_SUMMARY,
    RE_TARGET_PRICE,
    RE_THESIS,
)


class TestRE_RATING:
    def test_strong_buy(self) -> None:
        m = RE_RATING.search("**Rating**: Strong Buy")
        assert m is not None
        assert m.group(1) == "Strong Buy"

    def test_chinese_colon(self) -> None:
        m = RE_RATING.search("**Rating**：Buy")
        assert m is not None
        assert m.group(1) == "Buy"

    def test_no_rating(self) -> None:
        assert RE_RATING.search("No rating here") is None


class TestRE_THESIS:
    def test_basic_thesis(self) -> None:
        text = "**Investment Thesis**: This is the thesis.\n**Next**"
        m = RE_THESIS.search(text)
        assert m is not None
        assert "thesis" in m.group(1).strip()

    def test_no_thesis_tag(self) -> None:
        assert RE_THESIS.search("Just random text") is None


class TestRE_SUMMARY:
    def test_basic_summary(self) -> None:
        text = "**Executive Summary**: Brief summary.\n**Other**"
        m = RE_SUMMARY.search(text)
        assert m is not None
        assert "summary" in m.group(1).strip()

    def test_no_summary_tag(self) -> None:
        assert RE_SUMMARY.search("No summary here") is None


class TestRE_PCT:
    def test_positive_pct(self) -> None:
        m = RE_PCT.search("expected return of 12.5%")
        assert m is not None
        assert m.group(1) == "12.5"

    def test_negative_pct(self) -> None:
        m = RE_PCT.search("down 3.2%")
        assert m is not None
        assert m.group(1) == "3.2"

    def test_no_pct(self) -> None:
        assert RE_PCT.search("no percentage here") is None


class TestRE_POS_SIZE:
    def test_position_size(self) -> None:
        m = RE_POS_SIZE.search("仓位: 30% 以上")
        assert m is not None
        assert m.group(1) == "30"

    def test_position_size_with_right(self) -> None:
        m = RE_POS_SIZE.search("仓位 20% 左右")
        assert m is not None
        assert m.group(1) == "20"

    def test_no_position(self) -> None:
        assert RE_POS_SIZE.search("no position info") is None


class TestRE_STOP_LOSS:
    def test_range(self) -> None:
        m = RE_STOP_LOSS.search("止损: 140-145")
        assert m is not None

    def test_min_value(self) -> None:
        m = RE_STOP_LOSS.search("止损≥140")
        assert m is not None

    def test_no_stop_loss(self) -> None:
        assert RE_STOP_LOSS.search("no stop loss mentioned") is None


class TestRE_TARGET_PRICE:
    def test_range(self) -> None:
        m = RE_TARGET_PRICE.search("目标价: 200-220")
        assert m is not None

    def test_no_target(self) -> None:
        assert RE_TARGET_PRICE.search("no target price") is None


class TestRE_ENTRY_ZONE:
    def test_range(self) -> None:
        m = RE_ENTRY_ZONE.search("入场区间: 148-152")
        assert m is not None

    def test_no_entry(self) -> None:
        assert RE_ENTRY_ZONE.search("no entry zone") is None


class TestRE_HOLDING_PERIOD:
    def test_chinese(self) -> None:
        m = RE_HOLDING_PERIOD.search("持有期: 30天")
        assert m is not None
        assert m.group(1) == "30"

    def test_english(self) -> None:
        m = RE_HOLDING_PERIOD.search("holding period 60")
        assert m is not None
        assert m.group(1) == "60"

    def test_no_holding(self) -> None:
        assert RE_HOLDING_PERIOD.search("no holding period") is None


class TestRE_INVALIDATIONS:
    def test_basic(self) -> None:
        text = "失效条件: price drops below 100\n风险因素"
        m = RE_INVALIDATIONS.search(text)
        assert m is not None
        assert "100" in m.group(1)

    def test_no_invalidations(self) -> None:
        assert RE_INVALIDATIONS.search("nothing special here") is None


class TestRE_CATALYSTS:
    def test_basic(self) -> None:
        text = "**Catalysts**: earnings report\n**Next**"
        m = RE_CATALYSTS.search(text)
        assert m is not None
        assert "earnings" in m.group(1)

    def test_no_catalysts(self) -> None:
        assert RE_CATALYSTS.search("no catalysts mentioned") is None
