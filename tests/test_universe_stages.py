"""universe stages 过滤阶段的测试。"""

from __future__ import annotations

from trade_krono_cli.stock_filter import MaxValueRule
from trade_krono_cli.universe.provider import UniverseTicket
from trade_krono_cli.universe.stages.factor import FactorFilterStage
from trade_krono_cli.universe.stages.fundamental import FundamentalFilterStage
from trade_krono_cli.universe.stages.rules import FilterRulesStage
from trade_krono_cli.universe.stages.static import StaticFilterStage


def _make_ticket(
    ticker: str = "sh.600519",
    pe: float | None = 20.0,
    pb: float | None = 3.0,
    market_cap: float | None = 1000.0,
    is_st: bool = False,
    industry: str | None = "白酒",
    volume_ratio: float | None = 1.0,
    turnover_rate: float | None = 0.5,
    volume: float | None = 1_000_000.0,
    price: float | None = 100.0,
) -> UniverseTicket:
    return UniverseTicket(
        ticker=ticker,
        pe=pe,
        pb=pb,
        market_cap=market_cap,
        volume_ratio=volume_ratio,
        turnover_rate=turnover_rate,
        volume=volume,
        price=price,
        industry=industry,
        source="test",
    )


class TestStaticFilterStage:
    """静态过滤阶段：ST/停牌/次新/低价股。"""

    def test_keep_normal_stock(self) -> None:
        stage = StaticFilterStage()
        ticket = _make_ticket(ticker="sh.600519", price=100.0)
        result = stage.filter([ticket])
        assert len(result) == 1

    def test_exclude_low_price(self) -> None:
        stage = StaticFilterStage(exclude_low_price=True, low_price_threshold=5.0)
        ticket = _make_ticket(ticker="sh.600XXX", price=3.0)
        result = stage.filter([ticket])
        assert len(result) == 0

    def test_keep_above_price_threshold(self) -> None:
        stage = StaticFilterStage(exclude_low_price=True, low_price_threshold=5.0)
        ticket = _make_ticket(ticker="sh.600519", price=10.0)
        result = stage.filter([ticket])
        assert len(result) == 1

    def test_empty_input(self) -> None:
        stage = StaticFilterStage()
        result = stage.filter([])
        assert result == []


class TestFundamentalFilterStage:
    """基本面过滤阶段：PE/PB/市值/行业。"""

    def test_pass_normal_fundamentals(self) -> None:
        stage = FundamentalFilterStage()
        ticket = _make_ticket(pe=20.0, pb=3.0, market_cap=1000.0)
        result = stage.filter([ticket])
        assert len(result) == 1

    def test_exclude_high_pe(self) -> None:
        stage = FundamentalFilterStage(pe_range=(0.0, 16.0))
        ticket = _make_ticket(pe=25.0)
        result = stage.filter([ticket])
        assert len(result) == 0

    def test_exclude_high_pb(self) -> None:
        stage = FundamentalFilterStage(pb_range=(0.0, 3.0))
        ticket = _make_ticket(pb=5.0)
        result = stage.filter([ticket])
        assert len(result) == 0

    def test_exclude_small_market_cap(self) -> None:
        stage = FundamentalFilterStage(market_cap_min=500.0)
        ticket = _make_ticket(market_cap=100.0)
        result = stage.filter([ticket])
        assert len(result) == 0

    def test_exclude_negative_pe(self) -> None:
        # 默认不排除负PE（需显式设置 pe_range）
        stage = FundamentalFilterStage()
        ticket = _make_ticket(pe=-5.0)
        result = stage.filter([ticket])
        assert len(result) == 1

    def test_exclude_negative_pe_with_range(self) -> None:
        stage = FundamentalFilterStage(pe_range=(0.0, 100.0))
        ticket = _make_ticket(pe=-5.0)
        result = stage.filter([ticket])
        assert len(result) == 0

    def test_exclude_negative_pb(self) -> None:
        # 默认不排除负PB（需显式设置 pb_range 或 min_pb）
        stage = FundamentalFilterStage()
        ticket = _make_ticket(pb=-1.0)
        result = stage.filter([ticket])
        assert len(result) == 1

    def test_exclude_negative_pb_with_min_pb(self) -> None:
        stage = FundamentalFilterStage(min_pb=0.0)
        ticket = _make_ticket(pb=-1.0)
        result = stage.filter([ticket])
        assert len(result) == 0

    def test_industry_whitelist(self) -> None:
        stage = FundamentalFilterStage(industry_whitelist=["白酒", "医药"])
        ticket = _make_ticket(industry="白酒")
        result = stage.filter([ticket])
        assert len(result) == 1

    def test_industry_blacklist(self) -> None:
        stage = FundamentalFilterStage(industry_blacklist=["煤炭"])
        ticket = _make_ticket(industry="煤炭")
        result = stage.filter([ticket])
        assert len(result) == 0

    def test_industry_whitelist_rejects_other(self) -> None:
        stage = FundamentalFilterStage(industry_whitelist=["白酒"])
        ticket = _make_ticket(industry="煤炭")
        result = stage.filter([ticket])
        assert len(result) == 0

    def test_market_cap_range(self) -> None:
        stage = FundamentalFilterStage(market_cap_range=(50.0, 5000.0))
        ticket = _make_ticket(market_cap=100.0)
        result = stage.filter([ticket])
        assert len(result) == 1

        ticket2 = _make_ticket(market_cap=10000.0)
        result2 = stage.filter([ticket2])
        assert len(result2) == 0

    def test_empty_input(self) -> None:
        stage = FundamentalFilterStage()
        assert stage.filter([]) == []


class TestFactorFilterStage:
    """流动性过滤阶段：量比/换手率。"""

    def test_pass_normal_liquidity(self) -> None:
        stage = FactorFilterStage()
        ticket = _make_ticket(turnover_rate=0.5, volume_ratio=1.5)
        result = stage.filter([ticket])
        assert len(result) == 1

    def test_exclude_low_volume_ratio(self) -> None:
        stage = FactorFilterStage(min_volume_ratio=2.0)
        ticket = _make_ticket(volume_ratio=0.5)
        result = stage.filter([ticket])
        assert len(result) == 0

    def test_exclude_low_turnover(self) -> None:
        stage = FactorFilterStage(min_turnover_rate=1.0)
        ticket = _make_ticket(turnover_rate=0.05)
        result = stage.filter([ticket])
        assert len(result) == 0

    def test_exclude_low_volume(self) -> None:
        stage = FactorFilterStage(min_volume=10_000_000)
        ticket = _make_ticket(volume=1_000.0)
        result = stage.filter([ticket])
        assert len(result) == 0

    def test_empty_input(self) -> None:
        stage = FactorFilterStage()
        assert stage.filter([]) == []


class TestFilterRulesStage:
    """自定义规则链过滤。"""

    def test_pass_all_rules(self) -> None:
        rules: list[MaxValueRule] = [
            MaxValueRule(field="pe", value=30.0),
            MaxValueRule(field="pb", value=5.0),
        ]
        stage = FilterRulesStage(rules=rules)
        ticket = _make_ticket(pe=20.0, pb=3.0)
        result = stage.filter([ticket])
        assert len(result) == 1

    def test_fail_first_rule(self) -> None:
        rules: list[MaxValueRule] = [MaxValueRule(field="pe", value=15.0)]
        stage = FilterRulesStage(rules=rules)
        ticket = _make_ticket(pe=20.0)
        result = stage.filter([ticket])
        assert len(result) == 0

    def test_fail_second_rule(self) -> None:
        rules: list[MaxValueRule] = [
            MaxValueRule(field="pe", value=999.0),
            MaxValueRule(field="pb", value=2.0),
        ]
        stage = FilterRulesStage(rules=rules)
        ticket = _make_ticket(pb=3.0)
        result = stage.filter([ticket])
        assert len(result) == 0

    def test_empty_rules_passes_all(self) -> None:
        stage = FilterRulesStage(rules=[])
        ticket = _make_ticket()
        result = stage.filter([ticket])
        assert len(result) == 1

    def test_empty_input(self) -> None:
        stage = FilterRulesStage(rules=[])
        assert stage.filter([]) == []
