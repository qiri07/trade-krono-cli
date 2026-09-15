"""tests for trade_krono_cli.universe.stages.fundamental."""

from __future__ import annotations

from trade_krono_cli.universe.provider import UniverseTicket
from trade_krono_cli.universe.stages.fundamental import FundamentalFilterStage


def _make_ticket(
    ticker: str = "sh.600519",
    pe: float | None = 20.0,
    pb: float | None = 3.0,
    market_cap: float | None = 1000.0,
    industry: str | None = "白酒",
) -> UniverseTicket:
    return UniverseTicket(
        ticker=ticker,
        pe=pe,
        pb=pb,
        market_cap=market_cap,
        industry=industry,
        source="test",
    )


class TestFundamentalFilterStage:
    """Fundamental filter stage behavior."""

    def test_pass_normal_fundamentals(self) -> None:
        """Stock with normal PE/PB/market_cap should pass."""
        stage = FundamentalFilterStage()
        ticket = _make_ticket(pe=20.0, pb=3.0, market_cap=1000.0)
        result = stage.filter([ticket])
        assert len(result) == 1

    def test_exclude_high_pe(self) -> None:
        """Stock with PE above threshold should be filtered."""
        stage = FundamentalFilterStage(pe_range=(0.0, 16.0))
        ticket = _make_ticket(pe=25.0)
        result = stage.filter([ticket])
        assert len(result) == 0

    def test_exclude_high_pb(self) -> None:
        """Stock with PB above threshold should be filtered."""
        stage = FundamentalFilterStage(pb_range=(0.0, 3.0))
        ticket = _make_ticket(pb=5.0)
        result = stage.filter([ticket])
        assert len(result) == 0

    def test_exclude_small_market_cap(self) -> None:
        """Stock with market cap below min should be filtered."""
        stage = FundamentalFilterStage(market_cap_min=500.0)
        ticket = _make_ticket(market_cap=100.0)
        result = stage.filter([ticket])
        assert len(result) == 0

    def test_exclude_negative_pe(self) -> None:
        """PE <= 0 (loss-making) should be filtered when pe_range is set."""
        stage = FundamentalFilterStage(pe_range=(0.0, 100.0))
        ticket = _make_ticket(pe=-5.0)
        result = stage.filter([ticket])
        assert len(result) == 0

    def test_negative_pe_without_range_passes(self) -> None:
        """Negative PE without pe_range should not be filtered."""
        stage = FundamentalFilterStage()
        ticket = _make_ticket(pe=-5.0)
        result = stage.filter([ticket])
        assert len(result) == 1

    def test_industry_whitelist(self) -> None:
        """Stock in whitelist industry should pass."""
        stage = FundamentalFilterStage(industry_whitelist=["白酒", "医药"])
        ticket = _make_ticket(industry="白酒")
        result = stage.filter([ticket])
        assert len(result) == 1

    def test_industry_blacklist(self) -> None:
        """Stock in blacklist industry should be filtered."""
        stage = FundamentalFilterStage(industry_blacklist=["煤炭"])
        ticket = _make_ticket(industry="煤炭")
        result = stage.filter([ticket])
        assert len(result) == 0

    def test_industry_whitelist_rejects_other(self) -> None:
        """Stock not in whitelist should be filtered."""
        stage = FundamentalFilterStage(industry_whitelist=["白酒"])
        ticket = _make_ticket(industry="煤炭")
        result = stage.filter([ticket])
        assert len(result) == 0

    def test_market_cap_range(self) -> None:
        """Market cap in range should pass; out of range should fail."""
        stage = FundamentalFilterStage(market_cap_range=(50.0, 5000.0))
        ticket1 = _make_ticket(market_cap=100.0)
        ticket2 = _make_ticket(market_cap=10000.0)
        assert len(stage.filter([ticket1])) == 1
        assert len(stage.filter([ticket2])) == 0

    def test_empty_input(self) -> None:
        """Empty input should return empty list."""
        stage = FundamentalFilterStage()
        assert stage.filter([]) == []

    def test_name(self) -> None:
        assert FundamentalFilterStage().name == "fundamental"

    def test_na_values_handled(self) -> None:
        """None values for optional fields should not crash."""
        stage = FundamentalFilterStage(pe_range=(0.0, 100.0))
        ticket = _make_ticket(pe=None, pb=None, market_cap=None)
        result = stage.filter([ticket])
        # None values skip their respective checks, so it passes
        assert len(result) == 1

    def test_min_pb_filters_negative_pb(self) -> None:
        """Negative PB (bankrupt) should be filtered by min_pb."""
        stage = FundamentalFilterStage(min_pb=0.0)
        ticket = _make_ticket(pb=-1.0)
        result = stage.filter([ticket])
        assert len(result) == 0

    def test_min_pb_with_positive_pb_passes(self) -> None:
        """Positive PB above min should pass."""
        stage = FundamentalFilterStage(min_pb=0.0)
        ticket = _make_ticket(pb=1.0)
        result = stage.filter([ticket])
        assert len(result) == 1

    def test_multiple_conditions_combined(self) -> None:
        """Multiple filter conditions should all be applied."""
        stage = FundamentalFilterStage(
            pe_range=(0.0, 30.0),
            pb_range=(0.0, 5.0),
            market_cap_min=100.0,
        )
        ticket_good = _make_ticket(pe=20.0, pb=2.0, market_cap=500.0)
        ticket_bad_pe = _make_ticket(pe=50.0, pb=2.0, market_cap=500.0)
        ticket_bad_cap = _make_ticket(pe=20.0, pb=2.0, market_cap=50.0)
        result = stage.filter([ticket_good, ticket_bad_pe, ticket_bad_cap])
        assert len(result) == 1
        assert result[0].ticker == ticket_good.ticker
