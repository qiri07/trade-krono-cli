"""tests for trade_krono_cli.universe.stages.rules."""

from __future__ import annotations

from trade_krono_cli.stock_filter import ContainsRule, FilterOp, FilterRule, MatchRule
from trade_krono_cli.universe.provider import UniverseTicket
from trade_krono_cli.universe.stages.rules import FilterRulesStage


def _make_ticket(
    ticker: str = "sh.600519",
    pe: float | None = 20.0,
    pb: float | None = 3.0,
    market_cap: float | None = 1000.0,
    price: float | None = 100.0,
    industry: str | None = None,
) -> UniverseTicket:
    return UniverseTicket(
        ticker=ticker,
        pe=pe,
        pb=pb,
        market_cap=market_cap,
        price=price,
        industry=industry,
        source="test",
    )


class TestFilterRulesStage:
    """Custom rule filter stage behavior."""

    def test_pass_all_rules(self) -> None:
        """Ticket passing all rules should remain."""
        rules: list[FilterRule] = [
            FilterRule(field="pe", op=FilterOp.MAX, value=30.0),
            FilterRule(field="pb", op=FilterOp.MAX, value=5.0),
        ]
        stage = FilterRulesStage(rules=rules)
        ticket = _make_ticket(pe=20.0, pb=3.0)
        result = stage.filter([ticket])
        assert len(result) == 1

    def test_fail_first_rule(self) -> None:
        """Ticket failing first rule should be excluded."""
        rules: list[FilterRule] = [FilterRule(field="pe", op=FilterOp.MAX, value=15.0)]
        stage = FilterRulesStage(rules=rules)
        ticket = _make_ticket(pe=20.0)
        result = stage.filter([ticket])
        assert len(result) == 0

    def test_fail_second_rule(self) -> None:
        """Ticket passing first rule but failing second should be excluded."""
        rules: list[FilterRule] = [
            FilterRule(field="pe", op=FilterOp.MAX, value=999.0),
            FilterRule(field="pb", op=FilterOp.MAX, value=2.0),
        ]
        stage = FilterRulesStage(rules=rules)
        ticket = _make_ticket(pb=3.0)
        result = stage.filter([ticket])
        assert len(result) == 0

    def test_empty_rules_passes_all(self) -> None:
        """No rules means all tickets pass."""
        stage = FilterRulesStage(rules=[])
        ticket = _make_ticket()
        result = stage.filter([ticket])
        assert len(result) == 1

    def test_empty_input(self) -> None:
        """Empty input should return empty list."""
        stage = FilterRulesStage(rules=[])
        assert stage.filter([]) == []

    def test_name(self) -> None:
        assert FilterRulesStage().name == "rules"

    def test_min_op_filters_low_value(self) -> None:
        """MIN operator should keep tickets with value >= threshold."""
        rules: list[FilterRule] = [FilterRule(field="pe", op=FilterOp.MIN, value=10.0)]
        stage = FilterRulesStage(rules=rules)
        ticket_low = _make_ticket(pe=5.0)
        ticket_high = _make_ticket(pe=20.0)
        result = stage.filter([ticket_low, ticket_high])
        assert len(result) == 1
        assert result[0].ticker == ticket_high.ticker

    def test_range_op_filters_outside_range(self) -> None:
        """RANGE operator should keep tickets within [low, high]."""
        rules: list[FilterRule] = [
            FilterRule(field="market_cap", op=FilterOp.RANGE, value=(100.0, 5000.0))
        ]
        stage = FilterRulesStage(rules=rules)
        ticket_in = _make_ticket(market_cap=1000.0)
        ticket_out_low = _make_ticket(market_cap=50.0)
        ticket_out_high = _make_ticket(market_cap=10000.0)
        result = stage.filter([ticket_in, ticket_out_low, ticket_out_high])
        assert len(result) == 1
        assert result[0].ticker == ticket_in.ticker

    def test_in_op_filters_membership(self) -> None:
        """IN operator should keep tickets whose value is in the set."""
        rules: list[FilterRule] = [FilterRule(field="price", op=FilterOp.IN, value=[100.0, 200.0])]
        stage = FilterRulesStage(rules=rules)
        ticket_match = _make_ticket(price=100.0)
        ticket_no_match = _make_ticket(price=300.0)
        result = stage.filter([ticket_match, ticket_no_match])
        assert len(result) == 1
        assert result[0].ticker == ticket_match.ticker

    def test_none_value_skips_rule(self) -> None:
        """Ticket with None field should skip the rule (not fail)."""
        rules: list[FilterRule] = [FilterRule(field="pe", op=FilterOp.MAX, value=30.0)]
        stage = FilterRulesStage(rules=rules)
        ticket = _make_ticket(pe=None)
        result = stage.filter([ticket])
        assert len(result) == 1

    def test_multiple_ticketsMixed_results(self) -> None:
        """Mixed tickets should return only those passing all rules."""
        rules: list[FilterRule] = [
            FilterRule(field="pe", op=FilterOp.MAX, value=25.0),
            FilterRule(field="pb", op=FilterOp.MAX, value=4.0),
        ]
        stage = FilterRulesStage(rules=rules)
        tickets = [
            _make_ticket(pe=20.0, pb=3.0),  # pass
            _make_ticket(pe=30.0, pb=3.0),  # fail pe
            _make_ticket(pe=20.0, pb=5.0),  # fail pb
            _make_ticket(pe=25.0, pb=4.0),  # pass (boundary)
        ]
        result = stage.filter(tickets)
        assert len(result) == 2

    def test_alias_market_cap_billion(self) -> None:
        """market_cap_billion alias should resolve to market_cap field."""
        rules: list[FilterRule] = [
            FilterRule(field="market_cap_billion", op=FilterOp.MAX, value=500.0)
        ]
        stage = FilterRulesStage(rules=rules)
        ticket_under = _make_ticket(market_cap=100.0)
        ticket_over = _make_ticket(market_cap=1000.0)
        result = stage.filter([ticket_under, ticket_over])
        assert len(result) == 1
        assert result[0].ticker == ticket_under.ticker

    def test_alias_pe_ttm(self) -> None:
        """pe_ttm alias should resolve to pe field."""
        rules: list[FilterRule] = [FilterRule(field="pe_ttm", op=FilterOp.MAX, value=20.0)]
        stage = FilterRulesStage(rules=rules)
        ticket_under = _make_ticket(pe=15.0)
        ticket_over = _make_ticket(pe=25.0)
        result = stage.filter([ticket_under, ticket_over])
        assert len(result) == 1
        assert result[0].ticker == ticket_under.ticker

    def test_contains_op_filters_name(self) -> None:
        """CONTAINS operator should keep tickets whose field contains the substring."""
        rules: list[FilterRule] = [ContainsRule(field="industry", substr="白酒")]
        stage = FilterRulesStage(rules=rules)
        ticket_match = _make_ticket(industry="白酒集团")
        ticket_no_match = _make_ticket(industry="银行")
        result = stage.filter([ticket_match, ticket_no_match])
        assert len(result) == 1
        assert result[0].ticker == ticket_match.ticker

    def test_match_op_filters_with_regex(self) -> None:
        """MATCH operator should keep tickets whose field matches the regex."""
        rules: list[FilterRule] = [MatchRule(field="ticker", pattern=r"^sh\.6\d{5}$")]
        stage = FilterRulesStage(rules=rules)
        ticket_match = _make_ticket(ticker="sh.600519")
        ticket_no_match = _make_ticket(ticker="sz.000001")
        result = stage.filter([ticket_match, ticket_no_match])
        assert len(result) == 1
        assert result[0].ticker == ticket_match.ticker

    def test_not_in_op(self) -> None:
        """NOT_IN operator should exclude tickets in the set."""
        rules: list[FilterRule] = [
            FilterRule(field="ticker", op=FilterOp.NOT_IN, value=["sh.600ST"])
        ]
        stage = FilterRulesStage(rules=rules)
        ticket_in = _make_ticket(ticker="sh.600ST")
        ticket_out = _make_ticket(ticker="sh.600519")
        result = stage.filter([ticket_in, ticket_out])
        assert len(result) == 1
        assert result[0].ticker == ticket_out.ticker

    def test_unknown_op_returns_true(self) -> None:
        """Unknown FilterOp should fall through to return True (pass)."""
        from trade_krono_cli.universe.stages.rules import _apply_rule

        result = _apply_rule(10.0, "unknown_op", 5.0)
        assert result is True
