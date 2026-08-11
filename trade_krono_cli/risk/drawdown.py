"""
回撤风险模块 — Drawdown Risk。

计算基于 K 线价格的最大回撤，映射为 0-100 风险分。
"""
from __future__ import annotations

from typing import Tuple

import pandas as pd


def calc_drawdown_risk(high: pd.Series, close: pd.Series) -> Tuple[float, float]:
    """
    计算回撤风险分。

    逻辑：
      1. 计算滚动 60 日最高价
      2. 计算每日回撤百分比
      3. 取最大回撤的绝对值映射到风险分

    Parameters
    ----------
    high   : pd.Series 最高价
    close  : pd.Series 收盘价

    Returns
    -------
    (risk_score, max_drawdown_pct)
      risk_score          0-100，越高越危险
      max_drawdown_pct    最大回撤绝对值（%）
    """
    if len(close) < 30:
        return 20.0, 0.0

    # 60 日滚动最高价
    rolling_high = high.rolling(60, min_periods=1).max()
    drawdown = (close - rolling_high) / rolling_high * 100.0
    max_dd = drawdown.min()  # 最负值

    # 映射：-5%→20分，-20%→60分，-40%→100分
    # 线性插值
    abs_dd = abs(max_dd)
    if abs_dd < 5:
        risk_score = abs_dd / 5.0 * 20.0
    elif abs_dd > 40:
        risk_score = 100.0
    else:
        risk_score = 20.0 + (abs_dd - 5.0) / 35.0 * 80.0

    return round(min(100.0, max(0.0, risk_score)), 1), round(abs_dd, 2)
