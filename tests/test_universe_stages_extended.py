"""trade_krono_cli.universe 测试补充。"""

from __future__ import annotations

from unittest.mock import patch

from trade_krono_cli.universe.stages.factor import FactorFilterStage
from trade_krono_cli.universe.stages.fundamental import FundamentalFilterStage
from trade_krono_cli.universe.stages.rules import FilterRulesStage
from trade_krono_cli.universe.stages.static import StaticFilterStage


class _MockTicket:
    """模拟 UniverseTicket，用于传入 filter 方法。"""

    def __init__(
        self,
        ticker="mock",
        pe_ttm=None,
        pb=None,
        market_cap=None,
        turnover_rate=None,
        pe=None,
        volume_ratio=None,
        price=None,
        **kwargs,
    ):
        self.ticker = ticker
        self.pe_ttm = pe_ttm
        self.pb = pb
        self.market_cap = market_cap
        self.turnover_rate = turnover_rate
        self.pe = pe
        self.volume_ratio = volume_ratio
        self.price = price
        for k, v in kwargs.items():
            setattr(self, k, v)


class TestStaticFilterStage:
    """测试静态过滤阶段。"""

    def test_empty_input(self) -> None:
        """空输入返回空列表。"""
        stage = StaticFilterStage()
        assert stage.filter([]) == []

    def test_filter_st_stocks(self) -> None:
        """过滤 ST 股票（mock precheck_stock_status）。"""
        stage = StaticFilterStage(exclude_st=True, skip_suspended=False, skip_new_stock=False)
        tickets = [_MockTicket(ticker=t) for t in ["sh.600000", "sh.900001", "sz.000001"]]

        from trade_krono_cli.abnormal_stock import AbnormalityFlag, StockAbnormality

        mock_flags = {
            "sh.600000": AbnormalityFlag(ticker="sh.600000", flags=[]),
            "sh.900001": AbnormalityFlag(ticker="sh.900001", flags=[StockAbnormality.ST]),
            "sz.000001": AbnormalityFlag(ticker="sz.000001", flags=[]),
        }
        with patch(
            "trade_krono_cli.universe.stages.static.precheck_stock_status", return_value=mock_flags
        ):
            result = stage.filter(tickets)  # type: ignore[arg-type]
            assert all(t.ticker != "sh.900001" for t in result)

    def test_filter_suspended_stocks(self) -> None:
        """过滤停牌股票（mock precheck_stock_status）。"""
        stage = StaticFilterStage(exclude_st=False, skip_suspended=True, skip_new_stock=False)
        tickets = [_MockTicket(ticker=t) for t in ["sh.600000", "sh.600001"]]

        from trade_krono_cli.abnormal_stock import AbnormalityFlag, StockAbnormality

        mock_flags = {
            "sh.600000": AbnormalityFlag(ticker="sh.600000", flags=[]),
            "sh.600001": AbnormalityFlag(ticker="sh.600001", flags=[StockAbnormality.SUSPENDED]),
        }
        with patch(
            "trade_krono_cli.universe.stages.static.precheck_stock_status", return_value=mock_flags
        ):
            result = stage.filter(tickets)  # type: ignore[arg-type]
            assert all(t.ticker != "sh.600001" for t in result)


class TestFundamentalFilterStage:
    """测试基本面过滤阶段。"""

    def test_filter_by_pe_range(self) -> None:
        """按 PE 区间过滤（使用 pe 字段）。"""
        stage = FundamentalFilterStage(pe_range=(5.0, 20.0))
        stocks = [
            _MockTicket(ticker="sh.600000", pe=15),
            _MockTicket(ticker="sh.600001", pe=25),
        ]
        result = stage.filter(stocks)  # type: ignore[arg-type]
        assert len(result) == 1

    def test_filter_by_pb_range(self) -> None:
        """按 PB 区间过滤。"""
        stage = FundamentalFilterStage(pb_range=(0.5, 3.0))
        stocks = [
            _MockTicket(ticker="sh.600000", pb=2.0),
            _MockTicket(ticker="sh.600001", pb=4.0),
        ]
        result = stage.filter(stocks)  # type: ignore[arg-type]
        assert len(result) == 1


class TestFactorFilterStage:
    """测试因子过滤阶段。"""

    def test_filter_by_turnover(self) -> None:
        """按换手率过滤。"""
        stage = FactorFilterStage(min_turnover_rate=0.01)
        stocks = [
            _MockTicket(ticker="sh.600000", turnover_rate=0.05),
            _MockTicket(ticker="sh.600001", turnover_rate=0.005),
        ]
        result = stage.filter(stocks)  # type: ignore[arg-type]
        assert len(result) == 1


class TestFilterRulesStage:
    """测试规则链过滤阶段。"""

    def test_run_with_custom_rules(self) -> None:
        """应用自定义规则链。"""
        from trade_krono_cli.stock_filter import FilterOp, FilterRule

        rule = FilterRule(
            field="pe",
            op=FilterOp.MAX,
            value=20,
        )
        stage = FilterRulesStage(rules=[rule])
        stocks = [
            _MockTicket(ticker="sh.600000", pe=15),
            _MockTicket(ticker="sh.600001", pe=25),
        ]
        result = stage.filter(stocks)
        assert len(result) == 1
        assert result[0].ticker == "sh.600000"

    def test_empty_rules_passes_all(self) -> None:
        """空规则链应通过所有股票。"""
        stage = FilterRulesStage(rules=[])
        stocks = [
            _MockTicket(ticker="sh.600000"),
            _MockTicket(ticker="sh.600001"),
        ]
        result = stage.filter(stocks)
        assert len(result) == 2
