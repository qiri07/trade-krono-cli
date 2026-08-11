"""
波动率风险模块 — Volatility Risk。

计算基于 K 线日收益率的年化波动率，映射为 0-100 风险分。
"""
from __future__ import annotations

from typing import Tuple

import numpy as np
import pandas as pd


def calc_volatility_risk(close: pd.Series) -> Tuple[float, float]:
    """
    计算波动率风险分。

    逻辑：
      1. 计算日收益率
      2. 计算最近 20 日年化波动率
      3. 波动率越高，风险分越高（0-100）

    Parameters
    ----------
    close : pd.Series
        收盘价序列（至少 30 日）

    Returns
    -------
    (risk_score, annualized_vol_pct)
      risk_score       0-100，越高越危险
      annualized_vol_pct  年化波动率（%）
    """
    if len(close) < 30:
        return 25.0, 0.0  # 数据不足，给中等风险

    returns = close.pct_change().dropna()
    if len(returns) < 20:
        return 25.0, 0.0

    # 20 日年化波动率
    daily_vol = returns.tail(20).std()
    annualized_vol = daily_vol * np.sqrt(252) * 100  # 转百分比

    # 映射到 0-100 分：vol 0%→0分，vol 60%→100分（线性插值）
    risk_score = min(100.0, max(0.0, annualized_vol / 60.0 * 100.0))
    return round(risk_score, 1), round(annualized_vol, 2)
