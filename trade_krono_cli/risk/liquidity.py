"""
流动性风险模块 — Liquidity Risk。

计算基于成交量的流动性风险分，映射为 0-100 风险分。
"""
from __future__ import annotations

import math
from typing import Optional, Tuple

import pandas as pd

from trade_krono_cli.configs.risk import LiquidityThresholds


def calc_liquidity_risk(
    volume: pd.Series,
    market_cap: Optional[float] = None,
    thresholds: Optional[LiquidityThresholds] = None,
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
    thresholds : LiquidityThresholds  分段映射参数（可选，默认使用 schema 默认值）

    Returns
    -------
    (risk_score, avg_turnover_pct)
      risk_score      0-100，越高越危险
      avg_turnover_pct 日均换手率（%），无法计算时返回 None
    """
    th = thresholds or LiquidityThresholds()

    if len(volume) < th.insufficient_data_min_rows:
        return th.insufficient_data_score, None

    avg_volume = volume.tail(20).mean()
    log_vol = math.log1p(avg_volume)

    # 经验阈值映射（log 空间分段，从高流动性到低流动性）
    bps = th.breakpoints  # [(log1, score1), (log2, score2), ...]
    # 按 log 降序排列：log_vol >= 最高 threshold → 最低分
    sorted_bps = sorted(bps, key=lambda x: x[0], reverse=True)

    if log_vol >= sorted_bps[0][0]:
        # 超过最大 threshold：使用 tail_penalty_rate 递减
        risk_score = max(0.0, sorted_bps[0][1] - (log_vol - sorted_bps[0][0]) * th.tail_penalty_rate)
    elif log_vol < sorted_bps[-1][0]:
        # 低于最小 threshold：使用该点的分数
        risk_score = sorted_bps[-1][1]
    else:
        # 在两个 breakpoint 之间线性插值
        for i in range(len(sorted_bps) - 1):
            if sorted_bps[i + 1][0] <= log_vol < sorted_bps[i][0]:
                frac = (log_vol - sorted_bps[i + 1][0]) / (sorted_bps[i][0] - sorted_bps[i + 1][0])
                risk_score = sorted_bps[i + 1][1] + frac * (sorted_bps[i][1] - sorted_bps[i + 1][1])
                break
        else:
            risk_score = sorted_bps[-1][1]

    # 换手率（若有市值）
    avg_turnover = None
    if market_cap and market_cap > 0:
        # 近似：日均成交额 ≈ avg_volume * 10元（简化均价假设）
        # 换手率 = 日成交额 / 市值
        avg_turnover = round(avg_volume * 10.0 / (market_cap * 1e8) * 100, 4)

    return round(max(0.0, min(100.0, risk_score)), 1), avg_turnover
