"""tests for trade_krono_cli.universe.stages.factor."""

from __future__ import annotations

from trade_krono_cli.universe.provider import UniverseTicket
from trade_krono_cli.universe.stages.factor import FactorFilterStage


def _make_ticket(
    ticker: str = "sh.600519",
    volume_ratio: float | None = 1.0,
    turnover_rate: float | None = 0.5,
    volume: float | None = 1_000_000.0,
) -> UniverseTicket:
    return UniverseTicket(
        ticker=ticker,
        volume_ratio=volume_ratio,
        turnover_rate=turnover_rate,
        volume=volume,
        source="test",
    )


class TestFactorFilterStage:
    """Factor filter stage behavior."""

    def test_pass_normal_liquidity(self) -> None:
        """Stock with normal volume_ratio and turnover should pass."""
        stage = FactorFilterStage()
        ticket = _make_ticket(turnover_rate=0.5, volume_ratio=1.5)
        result = stage.filter([ticket])
        assert len(result) == 1

    def test_exclude_low_volume_ratio(self) -> None:
        """Stock with low volume_ratio should be filtered."""
        stage = FactorFilterStage(min_volume_ratio=2.0)
        ticket = _make_ticket(volume_ratio=0.5)
        result = stage.filter([ticket])
        assert len(result) == 0

    def test_exclude_low_turnover(self) -> None:
        """Stock with low turnover rate should be filtered."""
        stage = FactorFilterStage(min_turnover_rate=1.0)
        ticket = _make_ticket(turnover_rate=0.05)
        result = stage.filter([ticket])
        assert len(result) == 0

    def test_exclude_low_volume(self) -> None:
        """Stock with low absolute volume should be filtered."""
        stage = FactorFilterStage(min_volume=10_000_000)
        ticket = _make_ticket(volume=1_000.0)
        result = stage.filter([ticket])
        assert len(result) == 0

    def test_none_values_pass_when_not_configured(self) -> None:
        """None values for optional fields should not be filtered when no config."""
        stage = FactorFilterStage()
        ticket = _make_ticket(volume_ratio=None, turnover_rate=None, volume=None)
        result = stage.filter([ticket])
        assert len(result) == 1

    def test_none_volume_ratio_passes_with_min(self) -> None:
        """None volume_ratio should pass when min_volume_ratio is set."""
        stage = FactorFilterStage(min_volume_ratio=2.0)
        ticket = _make_ticket(volume_ratio=None)
        result = stage.filter([ticket])
        assert len(result) == 1

    def test_none_turnover_passes_with_min(self) -> None:
        """None turnover_rate should pass when min_turnover_rate is set."""
        stage = FactorFilterStage(min_turnover_rate=1.0)
        ticket = _make_ticket(turnover_rate=None)
        result = stage.filter([ticket])
        assert len(result) == 1

    def test_none_volume_passes_with_min(self) -> None:
        """None volume should pass when min_volume is set."""
        stage = FactorFilterStage(min_volume=10_000_000)
        ticket = _make_ticket(volume=None)
        result = stage.filter([ticket])
        assert len(result) == 1

    def test_all_three_filters_combined(self) -> None:
        """All three filter conditions should be applied together."""
        stage = FactorFilterStage(
            min_volume_ratio=1.0,
            min_turnover_rate=0.1,
            min_volume=500_000,
        )
        ticket_good = _make_ticket(volume_ratio=1.5, turnover_rate=0.5, volume=1_000_000)
        ticket_bad_ratio = _make_ticket(volume_ratio=0.3, turnover_rate=0.5, volume=1_000_000)
        ticket_bad_turnover = _make_ticket(volume_ratio=1.5, turnover_rate=0.01, volume=1_000_000)
        ticket_bad_volume = _make_ticket(volume_ratio=1.5, turnover_rate=0.5, volume=100)
        result = stage.filter(
            [ticket_good, ticket_bad_ratio, ticket_bad_turnover, ticket_bad_volume]
        )
        assert len(result) == 1
        assert result[0].ticker == ticket_good.ticker

    def test_empty_input(self) -> None:
        """Empty input should return empty list."""
        stage = FactorFilterStage()
        assert stage.filter([]) == []

    def test_name(self) -> None:
        assert FactorFilterStage().name == "factor"

    def test_high_volume_ratio_passes(self) -> None:
        """High volume_ratio should pass even with min set."""
        stage = FactorFilterStage(min_volume_ratio=2.0)
        ticket = _make_ticket(volume_ratio=5.0)
        result = stage.filter([ticket])
        assert len(result) == 1

    def test_high_turnover_passes(self) -> None:
        """High turnover_rate should pass even with min set."""
        stage = FactorFilterStage(min_turnover_rate=1.0)
        ticket = _make_ticket(turnover_rate=5.0)
        result = stage.filter([ticket])
        assert len(result) == 1
