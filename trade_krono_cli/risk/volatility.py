"""波动率风险模块 — Volatility Risk。

计算基于 K 线日收益率的年化波动率，映射为 0-100 风险分。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from trade_krono_cli.configs.risk import VolatilityThresholds

if TYPE_CHECKING:
    import pandas as pd


def calc_volatility_risk(
    close: pd.Series,
    thresholds: VolatilityThresholds | None = None,
) -> tuple[float, float]:
    """计算波动率风险分。

    逻辑：
      1. 计算日收益率
      2. 计算最近 20 日年化波动率
      3. 波动率越高，风险分越高（0-100）

    Parameters
    ----------
    close       : pd.Series  收盘价序列（至少 min_rows 日）
    thresholds  : VolatilityThresholds  分段映射参数（可选，默认使用 schema 默认值）

    Returns
    -------
    (risk_score, annualized_vol_pct)
      risk_score       0-100，越高越危险
      annualized_vol_pct  年化波动率（%）

    """
    th = thresholds or VolatilityThresholds()

    if len(close) < th.insufficient_data_min_rows:
        return th.insufficient_data_score, 0.0

    returns = close.pct_change().dropna()
    if len(returns) < 20:
        return th.insufficient_data_score, 0.0

    # 20 日年化波动率
    daily_vol = returns.tail(20).std()
    annualized_vol = daily_vol * np.sqrt(252) * 100  # 转百分比

    # 映射到 0-100 分：low_pct→0分，high_pct→100分（线性插值）
    risk_score = min(100.0, max(0.0, annualized_vol / th.high_pct * 100.0))
    return round(risk_score, 1), round(annualized_vol, 2)
