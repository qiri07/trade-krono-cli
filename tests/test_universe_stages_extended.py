#!/usr/bin/env python3
"""trade_krono_cli.universe 测试补充。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from trade_krono_cli.universe.stages.factor import FactorFilterStage
from trade_krono_cli.universe.stages.fundamental import FundamentalFilterStage
from trade_krono_cli.universe.stages.rules import FilterRulesStage
from trade_krono_cli.universe.stages.static import StaticFilterStage


class TestStaticFilterStage:
    """测试静态过滤阶段。"""

    def test_filter_st_stocks(self) -> None:
        """过滤 ST 股票。"""
        stage = StaticFilterStage()
        tickers = ["sh.600000", "sh.900001", "sz.000001"]  # 假设 900001 是 ST

        with patch.object(stage, "_is_st_stock", return_value=lambda t: t == "sh.900001"):
            result = stage.run(tickers)
            assert "sh.900001" not in result

    def test_filter_suspended_stocks(self) -> None:
        """过滤停牌股票。"""
        stage = StaticFilterStage()
        tickers = ["sh.600000", "sh.600001"]

        with patch.object(stage, "_is_suspended", return_value=lambda t: t == "sh.600001"):
            result = stage.run(tickers)
            assert "sh.600001" not in result


class TestFundamentalFilterStage:
    """测试基本面过滤阶段。"""

    def test_filter_by_pe(self) -> None:
        """按 PE 过滤。"""
        stage = FundamentalFilterStage(pe_max=20)
        stocks = [
            MagicMock(pe_ttm=15, pb=2.0, total_mv=100),
            MagicMock(pe_ttm=25, pb=3.0, total_mv=200),  # PE 超标
        ]

        result = stage.run(stocks)
        assert len(result) == 1

    def test_filter_by_pb(self) -> None:
        """按 PB 过滤。"""
        stage = FundamentalFilterStage(pb_max=3.0)
        stocks = [
            MagicMock(pe_ttm=15, pb=2.0, total_mv=100),
            MagicMock(pe_ttm=15, pb=4.0, total_mv=200),  # PB 超标
        ]

        result = stage.run(stocks)
        assert len(result) == 1


class TestFactorFilterStage:
    """测试因子过滤阶段。"""

    def test_filter_by_turnover(self) -> None:
        """按换手率过滤。"""
        stage = FactorFilterStage(min_turnover=0.01)
        stocks = [
            MagicMock(turnover_rate=0.05),
            MagicMock(turnover_rate=0.005),  # 换手率过低
        ]

        result = stage.run(stocks)
        assert len(result) == 1


class TestFilterRulesStage:
    """测试规则链过滤阶段。"""

    def test_run_with_custom_rules(self) -> None:
        """运行自定义规则。"""
        stage = FilterRulesStage(rules=[lambda s: s.pe_ttm < 20])
        stocks = [
            MagicMock(pe_ttm=15),
            MagicMock(pe_ttm=25),
        ]

        result = stage.run(stocks)
        assert len(result) == 1

    def test_empty_rules_passes_all(self) -> None:
        """空规则通过所有股票。"""
        stage = FilterRulesStage(rules=[])
        stocks = [MagicMock(pe_ttm=15), MagicMock(pe_ttm=25)]

        result = stage.run(stocks)
        assert len(result) == 2
