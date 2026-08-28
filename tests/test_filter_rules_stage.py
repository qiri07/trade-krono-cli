"""
Tests for FilterRulesStage and _apply_rule — covers lines 88-105 in rules.py.
"""

from __future__ import annotations

from trade_krono_cli.stock_filter import (
    FilterOp,
    FilterRule,
    InSetRule,
    MaxValueRule,
    MinValueRule,
)
from trade_krono_cli.universe.provider import UniverseTicket
from trade_krono_cli.universe.stages.rules import FilterRulesStage, _apply_rule, _get_field

# ── _get_field ────────────────────────────────────────────────────────────────


class TestGetField:
    def test_direct_field(self):
        t = UniverseTicket(ticker="sh.600519", pe=25.0, pb=3.0)
        assert _get_field(t, "pe") == 25.0
        assert _get_field(t, "pb") == 3.0
        assert _get_field(t, "price") is None

    def test_alias_field(self):
        t = UniverseTicket(ticker="sh.600519", pe=25.0, market_cap=5000.0)
        assert _get_field(t, "pe_ttm") == 25.0
        assert _get_field(t, "market_cap_billion") == 5000.0

    def test_missing_field_returns_none(self):
        t = UniverseTicket(ticker="sh.600519")
        assert _get_field(t, "nonexistent") is None

    def test_none_field_returns_none(self):
        t = UniverseTicket(ticker="sh.600519", pe=None)
        assert _get_field(t, "pe") is None


# ── _apply_rule ───────────────────────────────────────────────────────────────


class TestApplyRule:
    """Test all FilterOp branches in _apply_rule."""

    def test_min_operator_passes(self):
        assert _apply_rule(10.0, FilterOp.MIN, 5.0) is True

    def test_min_operator_fails(self):
        assert _apply_rule(3.0, FilterOp.MIN, 5.0) is False

    def test_max_operator_passes(self):
        assert _apply_rule(3.0, FilterOp.MAX, 5.0) is True

    def test_max_operator_fails(self):
        assert _apply_rule(10.0, FilterOp.MAX, 5.0) is False

    def test_range_operator_inside(self):
        assert _apply_rule(5.0, FilterOp.RANGE, (1.0, 10.0)) is True

    def test_range_operator_at_boundary_low(self):
        assert _apply_rule(1.0, FilterOp.RANGE, (1.0, 10.0)) is True

    def test_range_operator_at_boundary_high(self):
        assert _apply_rule(10.0, FilterOp.RANGE, (1.0, 10.0)) is True

    def test_range_operator_below(self):
        assert _apply_rule(0.5, FilterOp.RANGE, (1.0, 10.0)) is False

    def test_range_operator_above(self):
        assert _apply_rule(10.5, FilterOp.RANGE, (1.0, 10.0)) is False

    def test_in_operator_passes(self):
        assert _apply_rule("BUY", FilterOp.IN, {"BUY", "HOLD"}) is True

    def test_in_operator_fails(self):
        assert _apply_rule("SELL", FilterOp.IN, {"BUY", "HOLD"}) is False

    def test_not_in_operator_passes(self):
        assert _apply_rule("SELL", FilterOp.NOT_IN, {"BUY", "HOLD"}) is True

    def test_not_in_operator_fails(self):
        assert _apply_rule("BUY", FilterOp.NOT_IN, {"BUY", "HOLD"}) is False

    def test_contains_operator_passes(self):
        assert _apply_rule("贵州茅台", FilterOp.CONTAINS, "茅台") is True

    def test_contains_operator_fails(self):
        assert _apply_rule("贵州茅台", FilterOp.CONTAINS, "五粮液") is False

    def test_match_operator_passes(self):
        assert _apply_rule("贵州茅台", FilterOp.MATCH, r"茅台$") is True

    def test_match_operator_fails(self):
        assert _apply_rule("贵州茅台", FilterOp.MATCH, r"^五粮液") is False

    def test_type_error_returns_false(self):
        """When value/rule_value types are incompatible, return False."""
        assert _apply_rule("not_a_number", FilterOp.MIN, 5.0) is False

    def test_none_value_returns_false(self):
        """None value with numeric op raises TypeError → caught → returns False."""
        assert _apply_rule(None, FilterOp.MIN, 5.0) is False


# ── FilterRulesStage.filter ──────────────────────────────────────────────────


class TestFilterRulesStage:
    def test_empty_tickets(self):
        stage = FilterRulesStage(rules=[MinValueRule("pe", 10.0)])
        assert stage.filter([]) == []

    def test_empty_rules(self):
        stage = FilterRulesStage(rules=[])
        tickets = [UniverseTicket(ticker="sh.600519", pe=25.0)]
        assert stage.filter(tickets) == tickets

    def test_none_rules_init(self):
        stage = FilterRulesStage(rules=None)
        tickets = [UniverseTicket(ticker="sh.600519", pe=25.0)]
        assert stage.filter(tickets) == tickets

    def test_all_pass(self):
        stage = FilterRulesStage(
            rules=[
                MinValueRule("pe", 10.0),
                MaxValueRule("pb", 5.0),
            ]
        )
        tickets = [
            UniverseTicket(ticker="sh.600519", pe=25.0, pb=3.0),
            UniverseTicket(ticker="sz.000858", pe=20.0, pb=4.0),
        ]
        result = stage.filter(tickets)
        assert len(result) == 2

    def test_some_rejected(self):
        stage = FilterRulesStage(
            rules=[
                MinValueRule("pe", 10.0),
            ]
        )
        tickets = [
            UniverseTicket(ticker="sh.600519", pe=25.0),  # passes
            UniverseTicket(ticker="sz.000858", pe=5.0),  # fails (pe < 10)
        ]
        result = stage.filter(tickets)
        assert len(result) == 1
        assert result[0].ticker == "sh.600519"

    def test_none_field_skips_rule(self):
        """When a ticket has None for a field, that rule is skipped (not rejected)."""
        stage = FilterRulesStage(
            rules=[
                MinValueRule("pe", 10.0),
            ]
        )
        tickets = [
            UniverseTicket(ticker="sh.600519", pe=None),  # pe=None → skip rule → passes
        ]
        result = stage.filter(tickets)
        assert len(result) == 1

    def test_multiple_rules_all_must_pass(self):
        """All rules must pass; failure on any one rejects the ticket."""
        stage = FilterRulesStage(
            rules=[
                MinValueRule("pe", 10.0),
                MaxValueRule("pb", 3.0),
            ]
        )
        tickets = [
            UniverseTicket(ticker="sh.600519", pe=25.0, pb=2.0),  # passes both
            UniverseTicket(ticker="sz.000858", pe=20.0, pb=5.0),  # fails pb
            UniverseTicket(ticker="sh.601318", pe=5.0, pb=2.0),  # fails pe
        ]
        result = stage.filter(tickets)
        assert len(result) == 1
        assert result[0].ticker == "sh.600519"

    def test_contains_rule(self):
        """CONTAINS operator filters by substring match."""
        stage = FilterRulesStage(
            rules=[
                FilterRule("name", FilterOp.CONTAINS, "茅台", label="name contains 茅台"),
            ]
        )
        tickets = [
            UniverseTicket(ticker="sh.600519", name="贵州茅台"),
            UniverseTicket(ticker="sz.000858", name="五粮液"),
        ]
        result = stage.filter(tickets)
        assert len(result) == 1
        assert result[0].ticker == "sh.600519"

    def test_match_rule(self):
        """MATCH operator filters by regex."""
        # Note: MatchRule compiles the pattern, but _apply_rule uses str() on it.
        # Testing the raw MATCH branch directly with a string pattern.
        stage = FilterRulesStage(
            rules=[
                FilterRule("name", FilterOp.MATCH, r"茅台$", label="name match 茅台"),
            ]
        )
        tickets = [
            UniverseTicket(ticker="sh.600519", name="贵州茅台"),
            UniverseTicket(ticker="sz.000858", name="茅台集团"),
        ]
        result = stage.filter(tickets)
        assert len(result) == 1
        assert result[0].ticker == "sh.600519"

    def test_in_rule(self):
        """IN operator checks membership in a set."""
        stage = FilterRulesStage(
            rules=[
                InSetRule("name", {"贵州茅台", "五粮液"}),
            ]
        )
        tickets = [
            UniverseTicket(ticker="sh.600519", name="贵州茅台"),
            UniverseTicket(ticker="sz.000858", name="五粮液"),
            UniverseTicket(ticker="sh.601318", name="中国平安"),
        ]
        result = stage.filter(tickets)
        assert len(result) == 2

    def test_not_in_rule(self):
        """NOT_IN operator excludes items in a set."""
        stage = FilterRulesStage(
            rules=[
                FilterRule("name", FilterOp.NOT_IN, {"房地产"}, label="not realty"),
            ]
        )
        tickets = [
            UniverseTicket(ticker="sh.600519", name="贵州茅台"),
            UniverseTicket(ticker="sz.000858", name="万科企业"),
        ]
        result = stage.filter(tickets)
        assert len(result) == 2

    def test_range_rule(self):
        """RANGE operator checks value is within [low, high]."""
        stage = FilterRulesStage(
            rules=[
                FilterRule("pe", FilterOp.RANGE, (10.0, 30.0), label="pe range"),
            ]
        )
        tickets = [
            UniverseTicket(ticker="sh.600519", pe=25.0),
            UniverseTicket(ticker="sz.000858", pe=50.0),  # too high
            UniverseTicket(ticker="sh.601318", pe=5.0),  # too low
        ]
        result = stage.filter(tickets)
        assert len(result) == 1
        assert result[0].ticker == "sh.600519"

    def test_field_alias_market_cap_billion(self):
        """market_cap_billion alias maps to market_cap field."""
        stage = FilterRulesStage(
            rules=[
                MinValueRule("market_cap_billion", 100.0),
            ]
        )
        tickets = [
            UniverseTicket(ticker="sh.600519", market_cap=5000.0),
            UniverseTicket(ticker="sz.000858", market_cap=50.0),
        ]
        result = stage.filter(tickets)
        assert len(result) == 1
        assert result[0].ticker == "sh.600519"

    def test_field_alias_pe_ttm(self):
        """pe_ttm alias maps to pe field."""
        stage = FilterRulesStage(
            rules=[
                MinValueRule("pe_ttm", 10.0),
            ]
        )
        tickets = [
            UniverseTicket(ticker="sh.600519", pe=25.0),
            UniverseTicket(ticker="sz.000858", pe=5.0),
        ]
        result = stage.filter(tickets)
        assert len(result) == 1
        assert result[0].ticker == "sh.600519"
