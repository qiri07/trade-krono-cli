"""trade_krono_cli.risk.drawdown — 最大回撤风险单元测试。

使用 pandas Series 构造合成价格序列，验证回撤计算和风险分映射。
"""

from __future__ import annotations

import pandas as pd

from trade_krono_cli.configs.risk import DrawdownThresholds
from trade_krono_cli.risk.drawdown import calc_drawdown_risk


def _make_close(prices: list[float]) -> pd.Series:
    return pd.Series(prices, name="close")


def test_drawdown_no_drawdown_risk_score_low() -> None:
    """价格持续上涨，无回撤，风险分应接近 0。

    数据点需 >= insufficient_data_min_rows(30) 才会进入正常计算分支。
    """
    close = _make_close(list(range(100, 131)))  # 31 点，单调递增
    high = close.copy()
    score, max_dd = calc_drawdown_risk(high, close)
    assert max_dd == 0.0
    assert score == 0.0


def test_drawdown_significant_drawdown() -> None:
    """价格先涨后跌，应有可检测的最大回撤。"""
    close = _make_close(list(range(100, 131)) + [100, 90, 80])  # 34 点，先涨后跌
    high = close.copy()
    score, max_dd = calc_drawdown_risk(high, close)
    assert max_dd > 0
    assert 0 <= score <= 100


def test_drawdown_insufficient_data() -> None:
    """数据量不足时返回 insufficient_data_score。"""
    close = _make_close([100.0, 101.0])
    high = close.copy()
    th = DrawdownThresholds(insufficient_data_score=99.0, insufficient_data_min_rows=100)
    score, max_dd = calc_drawdown_risk(high, close, thresholds=th)
    assert score == 99.0
    assert max_dd == 0.0


def test_drawdown_custom_thresholds() -> None:
    """自定义阈值应影响风险分。"""
    close = _make_close([100, 110, 120, 130, 60, 50])
    high = close.copy()
    th = DrawdownThresholds(
        breakpoints=[(10.0, 10.0), (30.0, 50.0), (50.0, 90.0)],
        insufficient_data_score=25.0,
        insufficient_data_min_rows=5,
    )
    score, max_dd = calc_drawdown_risk(high, close, thresholds=th)
    assert 0 <= score <= 100
    assert max_dd > 0


def test_drawdown_default_thresholds() -> None:
    """默认阈值不应抛异常。"""
    close = _make_close([100, 105, 103, 107, 99, 95, 90, 92])
    high = close.copy()
    score, max_dd = calc_drawdown_risk(high, close)
    assert 0 <= score <= 100
    assert max_dd >= 0
