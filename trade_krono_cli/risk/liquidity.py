"""
流动性风险模块 — Liquidity Risk。

计算基于成交量的流动性风险分，映射为 0-100 风险分。
"""
from __future__ import annotations

import math
from typing import Optional, Tuple

import pandas as pd


def calc_liquidity_risk(
    volume: pd.Series,
    market_cap: Optional[float] = None,
) -> Tuple[float, Optional[float]]:
    """
    计算流动性风险分。

    逻辑：
      1. 计算近 20 日平均成交量
      2. 成交量越小，风险越高
      3. 若有市值，额外计算日均换手率

    Parameters
    ----------
    volume     : pd.Series 成交量（股）
    market_cap : float or None，市值（亿元）

    Returns
    -------
    (risk_score, avg_turnover_pct)
      risk_score      0-100，越高越危险
      avg_turnover_pct 日均换手率（%），无法计算时返回 None
    """
    if len(volume) < 10:
        return 30.0, None

    avg_volume = volume.tail(20).mean()
    log_vol = math.log1p(avg_volume)

    # 经验阈值映射（log 空间分段）
    if log_vol < 5:      # < 150 万股/日
        risk_score = 80.0
    elif log_vol < 6:    # < 546 万股/日
        risk_score = 60.0
    elif log_vol < 7:    # < 2980 万股/日
        risk_score = 40.0
    elif log_vol < 8:    # < 1.6 亿股/日
        risk_score = 20.0
    else:
        risk_score = max(0.0, 20.0 - (log_vol - 8.0) * 5.0)

    # 换手率（若有市值）
    avg_turnover = None
    if market_cap and market_cap > 0:
        # 近似：日均成交额 ≈ avg_volume * 10元（简化均价假设）
        # 换手率 = 日成交额 / 市值
        avg_turnover = round(avg_volume * 10.0 / (market_cap * 1e8) * 100, 4)

    return round(max(0.0, min(100.0, risk_score)), 1), avg_turnover
