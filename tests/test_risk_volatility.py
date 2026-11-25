"""trade_krono_cli.risk.volatility — 波动率风险单元测试。

使用 pandas Series 构造合成价格序列，验证年化波动率计算和风险分映射。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from trade_krono_cli.configs.risk import VolatilityThresholds
from trade_krono_cli.risk.volatility import calc_volatility_risk


def _make_close(prices: list[float]) -> pd.Series:
    return pd.Series(prices, name="close")


def test_volatility_constant_price() -> None:
    """价格不变，波动率为 0，风险分应为 0。"""
    close = _make_close([100.0] * 50)
    score, vol = calc_volatility_risk(close)
    assert vol == 0.0
    assert score == 0.0


def test_volatility_insufficient_data() -> None:
    """数据量不足时返回 insufficient_data_score。"""
    close = _make_close([100.0, 101.0])
    th = VolatilityThresholds(
        insufficient_data_score=42.0,
        insufficient_data_min_rows=100,
    )
    score, vol = calc_volatility_risk(close, thresholds=th)
    assert score == 42.0
    assert vol == 0.0


def test_volatility_high_volatility() -> None:
    """高波动率价格序列应产生较高风险分。"""
    rng = np.random.default_rng(42)
    returns = rng.normal(0, 0.05, 100)  # 每日 5% 波动
    prices = 100 * np.exp(np.cumsum(returns))
    close = _make_close(prices.tolist())
    score, vol = calc_volatility_risk(close)
    assert 0 <= score <= 100
    assert vol > 0


def test_volatility_custom_thresholds() -> None:
    """自定义高阈值应导致较低风险分。"""
    close = _make_close([100.0, 102.0, 98.0, 104.0, 96.0] * 10)
    th = VolatilityThresholds(high_pct=200.0)
    score, vol = calc_volatility_risk(close, thresholds=th)
    assert 0 <= score <= 100
    # 高阈值下，相同波动率对应较低风险分
    assert score < 50
