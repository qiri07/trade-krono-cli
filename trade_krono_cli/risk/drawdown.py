"""
回撤风险模块 — Drawdown Risk。

计算基于 K 线价格的最大回撤，映射为 0-100 风险分。
"""
from __future__ import annotations

from typing import Optional, Tuple

import pandas as pd

from trade_krono_cli.configs.risk import DrawdownThresholds


def calc_drawdown_risk(
    high: pd.Series,
    close: pd.Series,
    thresholds: Optional[DrawdownThresholds] = None,
) -> Tuple[float, float]:
    """
    计算回撤风险分。

    逻辑：
      1. 计算滚动 60 日最高价
      2. 计算每日回撤百分比
      3. 取最大回撤的绝对值按 breakpoints 分段映射到风险分

    Parameters
    ----------
    high       : pd.Series 最高价
    close      : pd.Series 收盘价
    thresholds : DrawdownThresholds  分段映射参数（可选，默认使用 schema 默认值）

    Returns
    -------
    (risk_score, max_drawdown_pct)
      risk_score          0-100，越高越危险
      max_drawdown_pct    最大回撤绝对值（%）
    """
    th = thresholds or DrawdownThresholds()

    if len(close) < th.insufficient_data_min_rows:
        return th.insufficient_data_score, 0.0

    # 60 日滚动最高价
    rolling_high = high.rolling(60, min_periods=1).max()
    drawdown = (close - rolling_high) / rolling_high * 100.0
    max_dd = drawdown.min()  # 最负值

    abs_dd = abs(max_dd)
    bps = th.breakpoints  # [(a1,s1), (a2,s2), (a3,s3)]

    if abs_dd < bps[0][0]:
        risk_score = abs_dd / bps[0][0] * bps[0][1]
    elif abs_dd > bps[-1][0]:
        risk_score = 100.0
    else:
        # 在两个 breakpoint 之间线性插值
        for i in range(len(bps) - 1):
            if bps[i][0] <= abs_dd <= bps[i + 1][0]:
                frac = (abs_dd - bps[i][0]) / (bps[i + 1][0] - bps[i][0])
                risk_score = bps[i][1] + frac * (bps[i + 1][1] - bps[i][1])
                break
        else:
            risk_score = bps[-1][1]

    return round(min(100.0, max(0.0, risk_score)), 1), round(abs_dd, 2)
