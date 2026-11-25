"""trade_krono_cli.risk.market_regime — 市场环境风险单元测试。

验证基于动量的市场环境风险分计算。
"""

from __future__ import annotations

import pandas as pd

from trade_krono_cli.configs.risk import MarketRegimeThresholds
from trade_krono_cli.risk.market_regime import calc_market_regime_risk


def _make_close(prices: list[float]) -> pd.Series:
    return pd.Series(prices, name="close")


def test_bull_market_low_risk() -> None:
    """持续上涨趋势，风险分应较低。"""
    prices = [100.0 + i for i in range(100)]
    close = _make_close(prices)
    score = calc_market_regime_risk(close)
    assert 0 <= score <= 50


def test_bear_market_high_risk() -> None:
    """持续下跌趋势，风险分应较高。"""
    prices = [100.0 - i * 0.5 for i in range(100)]
    close = _make_close(prices)
    score = calc_market_regime_risk(close)
    assert score >= 50


def test_insufficient_data() -> None:
    """数据量不足返回 insufficient_data_score。"""
    close = _make_close([100.0, 101.0])
    th = MarketRegimeThresholds(
        insufficient_data_score=77.0,
        insufficient_data_min_rows=100,
    )
    score = calc_market_regime_risk(close, thresholds=th)
    assert score == 77.0


def test_flat_market_neutral_risk() -> None:
    """横盘震荡，风险分应在中等区间。"""
    prices = [100.0] * 50 + [101.0] * 50
    close = _make_close(prices)
    score = calc_market_regime_risk(close)
    assert 20 <= score <= 80


def test_custom_thresholds() -> None:
    """自定义阈值应影响风险分。"""
    prices = [100.0] * 100
    close = _make_close(prices)
    th = MarketRegimeThresholds(
        bear_threshold=-5.0,
        neutral_low=-1.0,
        neutral_high=1.0,
        bear_score=90.0,
        neutral_mid_score=60.0,
        bull_base_score=10.0,
        insufficient_data_score=30.0,
        insufficient_data_min_rows=5,
    )
    score = calc_market_regime_risk(close, thresholds=th)
    assert 0 <= score <= 100
