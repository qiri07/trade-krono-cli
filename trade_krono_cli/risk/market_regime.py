"""
市场环境风险模块 — Market Regime Risk。

计算基于趋势动量的市场环境风险分，映射为 0-100 风险分。
"""
from __future__ import annotations

import pandas as pd


def calc_market_regime_risk(close: pd.Series) -> float:
    """
    计算市场环境风险分。

    逻辑：
      1. 计算 20 日动量（短期涨跌）
      2. 计算 60 日趋势（中长期方向）
      3. 趋势越弱/下跌，风险越高

    Parameters
    ----------
    close : pd.Series 收盘价序列

    Returns
    -------
    risk_score : 0-100，越高越危险
    """
    if len(close) < 30:
        return 30.0

    momentum_20 = (close.iloc[-1] - close.iloc[-20]) / close.iloc[-20] * 100
    if len(close) >= 60:
        momentum_60 = (close.iloc[-1] - close.iloc[-60]) / close.iloc[-60] * 100
    else:
        momentum_60 = momentum_20

    avg_momentum = (momentum_20 + momentum_60) / 2.0

    # 动量映射：
    #   <= -10% → 80 分（强烈下跌趋势，高风险）
    #   -10%~0% → 50-80 分（递减风险）
    #   0%~10%  → 20-50 分（温和上涨）
    #   > 10%   → 0-20 分（强势上涨，低风险）
    if avg_momentum <= -10:
        risk_score = 80.0
    elif avg_momentum <= 0:
        risk_score = 50.0 + (-avg_momentum / 10.0) * 30.0
    elif avg_momentum <= 10:
        risk_score = max(0.0, 50.0 - (avg_momentum / 10.0) * 30.0)
    else:
        risk_score = max(0.0, 20.0 - (avg_momentum - 10.0) / 10.0 * 20.0)

    return round(max(0.0, min(100.0, risk_score)), 1)
