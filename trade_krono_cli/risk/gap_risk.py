"""
Gap Risk — 跳空缺口风险。

基于收盘价突变的缺口频率计算风险分。
"""
from __future__ import annotations

import pandas as pd

from trade_krono_cli.risk.models import gap_risk_score as _gap_risk_score


def calc_gap_risk(
    close: pd.Series,
    high: pd.Series,
    low: pd.Series,
    min_gap_pct: float = 3.0,
) -> float:
    """
    计算缺口风险分（0-100）。

    Parameters
    ----------
    close       : 收盘价序列
    high        : 最高价序列
    low         : 最低价序列
    min_gap_pct : 最小缺口百分比阈值（默认 3%）

    Returns
    -------
    float : 缺口风险分 0-100
    """
    return _gap_risk_score(close, high, low, min_gap_pct)
