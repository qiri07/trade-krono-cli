"""risk 各风险模块的测试。"""

from __future__ import annotations

import pandas as pd

from trade_krono_cli.risk.concentration import calc_concentration_risk
from trade_krono_cli.risk.drawdown import calc_drawdown_risk
from trade_krono_cli.risk.event_risk import calc_event_risk
from trade_krono_cli.risk.gap_risk import calc_gap_risk
from trade_krono_cli.risk.liquidity import calc_liquidity_risk
from trade_krono_cli.risk.market_regime import calc_market_regime_risk
from trade_krono_cli.risk.valuation_risk import calc_valuation_risk
from trade_krono_cli.risk.volatility import calc_volatility_risk


def _s(values: list[float]) -> pd.Series:
    return pd.Series(values)


def _score(result) -> float:
    """从返回值中提取分数（兼容 tuple 和单值）。"""
    if isinstance(result, tuple):
        return float(result[0])
    return float(result)


class TestVolatilityRisk:
    """波动率风险计算。"""

    def test_normal_volatility(self) -> None:
        closes = _s([100 + i * 0.5 for i in range(100)])
        score = _score(calc_volatility_risk(closes))
        assert 0 <= score <= 100

    def test_high_volatility(self) -> None:
        closes = _s([100 + (i % 20 - 10) * 5 for i in range(100)])
        score = _score(calc_volatility_risk(closes))
        assert score > 20

    def test_low_volatility(self) -> None:
        closes = _s([100.0 + i * 0.01 for i in range(100)])
        score = _score(calc_volatility_risk(closes))
        assert score < 20

    def test_empty_series(self) -> None:
        score = _score(calc_volatility_risk(_s([])))
        assert isinstance(score, float)

    def test_single_value(self) -> None:
        score = _score(calc_volatility_risk(_s([100.0])))
        assert isinstance(score, float)


class TestDrawdownRisk:
    """回撤风险计算。"""

    def test_uptrend_low_drawdown(self) -> None:
        high = _s([101 + i * 0.5 for i in range(100)])
        close = _s([100 + i * 0.5 for i in range(100)])
        score = _score(calc_drawdown_risk(high, close))
        assert score >= 0

    def test_v_shaped_recovery(self) -> None:
        high = _s([100.0] * 7)
        close = _s([100, 80, 60, 70, 90, 100, 110])
        score = _score(calc_drawdown_risk(high, close))
        assert score > 0

    def test_empty_series(self) -> None:
        score = _score(calc_drawdown_risk(_s([]), _s([])))
        assert isinstance(score, float)


class TestConcentrationRisk:
    """集中度风险计算。"""

    def test_single_stock(self) -> None:
        score = calc_concentration_risk([1.0])
        assert score > 0

    def test_diversified_portfolio(self) -> None:
        score = calc_concentration_risk([0.1] * 10)
        assert score < 50

    def test_two_equal_weights(self) -> None:
        score = calc_concentration_risk([0.5, 0.5])
        assert score > 0

    def test_empty_weights(self) -> None:
        score = calc_concentration_risk([])
        assert score >= 0


class TestLiquidityRisk:
    """流动性风险计算。"""

    def test_high_volume_liquid(self) -> None:
        score = _score(calc_liquidity_risk(_s([10_000_000] * 100), market_cap=1000.0))
        assert score < 80

    def test_low_volume_illiquid(self) -> None:
        score = _score(calc_liquidity_risk(_s([10_000] * 100), market_cap=100.0))
        assert score > 10

    def test_empty_series(self) -> None:
        score = _score(calc_liquidity_risk(_s([])))
        assert isinstance(score, float)


class TestGapRisk:
    """跳空缺口风险。"""

    def test_normal_no_gap(self) -> None:
        close = _s([100 + i * 0.1 for i in range(100)])
        high = close * 1.01
        low = close * 0.99
        score = _score(calc_gap_risk(close, high, low))
        assert score < 30

    def test_gap_down_event(self) -> None:
        close = _s([100, 90, 95, 100])
        high = _s([100, 91, 96, 101])
        low = _s([99, 89, 94, 99])
        score = _score(calc_gap_risk(close, high, low))
        assert score >= 0

    def test_empty_series(self) -> None:
        score = _score(calc_gap_risk(_s([]), _s([]), _s([])))
        assert isinstance(score, float)


class TestEventRisk:
    """事件风险。"""

    def test_no_events(self) -> None:
        close = _s([100 + i * 0.1 for i in range(100)])
        score = _score(calc_event_risk(close))
        assert score >= 0

    def test_limit_down_event(self) -> None:
        close = _s([100, 90, 81, 73])
        score = _score(calc_event_risk(close))
        assert score > 0


class TestValuationRisk:
    """估值风险。"""

    def test_normal_pe(self) -> None:
        score = calc_valuation_risk(pe_ttm=20.0, pb=2.0)
        assert 0 <= score <= 100

    def test_high_pe(self) -> None:
        score = calc_valuation_risk(pe_ttm=100.0, pb=10.0)
        assert score > 50

    def test_negative_pe(self) -> None:
        score = calc_valuation_risk(pe_ttm=-5.0, pb=1.0)
        assert score > 30

    def test_zero_pe(self) -> None:
        score = calc_valuation_risk(pe_ttm=0.0, pb=0.0)
        assert score >= 0

    def test_none_values(self) -> None:
        score = calc_valuation_risk()
        assert score >= 0


class TestMarketRegimeRisk:
    """市场状态风险。"""

    def test_bull_market(self) -> None:
        close = _s([100 + i * 0.5 for i in range(200)])
        score = _score(calc_market_regime_risk(close))
        assert score < 50

    def test_bear_market(self) -> None:
        close = _s([100 - i * 0.3 for i in range(200)])
        score = _score(calc_market_regime_risk(close))
        assert score > 10

    def test_empty_series(self) -> None:
        score = _score(calc_market_regime_risk(_s([])))
        assert isinstance(score, float)
