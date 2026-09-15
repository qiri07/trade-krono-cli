"""tests for trade_krono_cli.universe.stages.static."""

from __future__ import annotations

from unittest.mock import patch

from trade_krono_cli.abnormal_stock import AbnormalityFlag, StockAbnormality
from trade_krono_cli.universe.provider import UniverseTicket
from trade_krono_cli.universe.stages.static import StaticFilterStage


def _make_ticket(
    ticker: str = "sh.600519",
    price: float | None = 100.0,
) -> UniverseTicket:
    return UniverseTicket(ticker=ticker, price=price, source="test")


def _make_flag(*flags: StockAbnormality) -> AbnormalityFlag:
    """Create an AbnormalityFlag with the given flags."""
    return AbnormalityFlag(ticker="", flags=list(flags))


class TestStaticFilterStage:
    """Static filter stage behavior."""

    def test_keep_normal_stock(self) -> None:
        """Normal stock with reasonable price should pass."""
        stage = StaticFilterStage()
        ticket = _make_ticket(ticker="sh.600519", price=100.0)
        result = stage.filter([ticket])
        assert len(result) == 1

    def test_keep_above_price_threshold(self) -> None:
        """Stock above low-price threshold should pass."""
        stage = StaticFilterStage(exclude_low_price=True, low_price_threshold=5.0)
        ticket = _make_ticket(ticker="sh.600519", price=10.0)
        result = stage.filter([ticket])
        assert len(result) == 1

    def test_exclude_low_price(self) -> None:
        """Stock below low-price threshold should be filtered."""
        stage = StaticFilterStage(exclude_low_price=True, low_price_threshold=5.0)
        ticket = _make_ticket(ticker="sh.600XXX", price=3.0)
        result = stage.filter([ticket])
        assert len(result) == 0

    def test_empty_input(self) -> None:
        """Empty input should return empty list."""
        stage = StaticFilterStage()
        result = stage.filter([])
        assert result == []

    def test_none_price_passes(self) -> None:
        """Stock with unknown price (None) should not be filtered by price."""
        stage = StaticFilterStage(exclude_low_price=True, low_price_threshold=5.0)
        ticket = _make_ticket(ticker="sh.600519", price=None)
        result = stage.filter([ticket])
        assert len(result) == 1

    def test_name(self) -> None:
        assert StaticFilterStage().name == "static"

    def test_default_batch_size(self) -> None:
        """Default batch size should be 200."""
        stage = StaticFilterStage()
        assert stage.batch_size == 200

    def test_disable_exclude_low_price(self) -> None:
        """When exclude_low_price=False, low-priced stocks should pass."""
        stage = StaticFilterStage(exclude_low_price=False)
        ticket = _make_ticket(ticker="sh.600XXX", price=1.0)
        result = stage.filter([ticket])
        assert len(result) == 1

    def test_mixed_tickets(self) -> None:
        """Mix of passing and failing tickets should return only passing ones."""
        stage = StaticFilterStage(exclude_low_price=True, low_price_threshold=5.0)
        tickets = [
            _make_ticket(ticker="sh.600519", price=100.0),
            _make_ticket(ticker="sh.600XXX", price=3.0),
            _make_ticket(ticker="sz.000001", price=15.0),
        ]
        result = stage.filter(tickets)
        assert len(result) == 2
        tickers = {t.ticker for t in result}
        assert "sh.600519" in tickers
        assert "sz.000001" in tickers
        assert "sh.600XXX" not in tickers

    def test_st_stock_rejected(self) -> None:
        """Stock flagged as ST should be rejected even if precheck returns it."""
        stage = StaticFilterStage()
        ticket = _make_ticket(ticker="sh.600ST", price=5.0)
        with patch(
            "trade_krono_cli.universe.stages.static.precheck_stock_status",
            return_value={"sh.600ST": _make_flag(StockAbnormality.ST)},
        ):
            result = stage.filter([ticket])
        assert len(result) == 0

    def test_suspended_stock_rejected(self) -> None:
        """Stock flagged as SUSPENDED should be rejected."""
        stage = StaticFilterStage(skip_suspended=True)
        ticket = _make_ticket(ticker="sh.600SUS", price=5.0)
        with patch(
            "trade_krono_cli.universe.stages.static.precheck_stock_status",
            return_value={"sh.600SUS": _make_flag(StockAbnormality.SUSPENDED)},
        ):
            result = stage.filter([ticket])
        assert len(result) == 0

    def test_new_stock_rejected(self) -> None:
        """Recently listed stock should be rejected when skip_new_stock=True."""
        stage = StaticFilterStage(skip_new_stock=True, new_stock_min_days=60)
        ticket = _make_ticket(ticker="sh.600NEW", price=50.0)
        with patch(
            "trade_krono_cli.universe.stages.static.precheck_stock_status",
            return_value={"sh.600NEW": _make_flag(StockAbnormality.NEW_STOCK)},
        ):
            result = stage.filter([ticket])
        assert len(result) == 0

    def test_precheck_exception_fallback(self) -> None:
        """When precheck raises, all tickets in the batch should pass through."""
        stage = StaticFilterStage()
        ticket = _make_ticket(ticker="sh.600519", price=100.0)
        with patch(
            "trade_krono_cli.universe.stages.static.precheck_stock_status",
            side_effect=RuntimeError("API down"),
        ):
            result = stage.filter([ticket])
        assert len(result) == 1

    def test_precheck_returns_none_for_ticker(self) -> None:
        """When precheck returns None for a ticker, it passes through."""
        stage = StaticFilterStage()
        ticket = _make_ticket(ticker="sh.600519", price=100.0)
        with patch(
            "trade_krono_cli.universe.stages.static.precheck_stock_status",
            return_value={},  # No flags returned for any ticker
        ):
            result = stage.filter([ticket])
        assert len(result) == 1

    def test_low_price_in_batch_loop(self) -> None:
        """Low-price ticket should be rejected inside batch loop when precheck has no flags."""
        stage = StaticFilterStage(exclude_low_price=True, low_price_threshold=5.0)
        ticket = _make_ticket(ticker="sh.600LOW", price=3.0)
        # precheck returns empty dict so the ticket has no abnormal flags
        # but the low-price check inside the batch loop should still reject it
        with patch(
            "trade_krono_cli.universe.stages.static.precheck_stock_status",
            return_value={},
        ):
            result = stage.filter([ticket])
        assert len(result) == 0

    def test_disable_skip_new_stock_allows_new_stock(self) -> None:
        """When skip_new_stock=False, new stocks should pass."""
        stage = StaticFilterStage(skip_new_stock=False)
        ticket = _make_ticket(ticker="sh.600NEW", price=50.0)
        with patch(
            "trade_krono_cli.universe.stages.static.precheck_stock_status",
            return_value={"sh.600NEW": _make_flag(StockAbnormality.NEW_STOCK)},
        ):
            result = stage.filter([ticket])
        assert len(result) == 1

    def test_disable_exclude_st_allows_st(self) -> None:
        """When exclude_st=False, ST stocks should pass."""
        stage = StaticFilterStage(exclude_st=False)
        ticket = _make_ticket(ticker="sh.600ST", price=5.0)
        with patch(
            "trade_krono_cli.universe.stages.static.precheck_stock_status",
            return_value={"sh.600ST": _make_flag(StockAbnormality.ST)},
        ):
            result = stage.filter([ticket])
        assert len(result) == 1

    def test_multiple_tickets_batch(self) -> None:
        """Multiple tickets with mixed flags should correctly filter."""
        stage = StaticFilterStage()
        tickets = [
            _make_ticket(ticker="sh.600519", price=100.0),
            _make_ticket(ticker="sh.600ST1", price=100.0),
            _make_ticket(ticker="sh.600SUS", price=100.0),
        ]
        with patch(
            "trade_krono_cli.universe.stages.static.precheck_stock_status",
            return_value={
                "sh.600519": _make_flag(),  # normal
                "sh.600ST1": _make_flag(StockAbnormality.ST),
                "sh.600SUS": _make_flag(StockAbnormality.SUSPENDED),
            },
        ):
            result = stage.filter(tickets)
        assert len(result) == 1
        assert result[0].ticker == "sh.600519"
