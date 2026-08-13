"""
Valuation Risk — 估值风险。

基于 PE/PB/市值的多维度估值风险评分。
"""
from __future__ import annotations

from typing import Optional

from trade_krono_cli.risk.models import valuation_risk_score as _valuation_risk_score


def calc_valuation_risk(
    pe_ttm: Optional[float] = None,
    pb: Optional[float] = None,
    market_cap_billion: Optional[float] = None,
) -> float:
    """
    计算估值风险分（0-100）。

    Parameters
    ----------
    pe_ttm               : 市盈率 TTM（None 表示无数据）
    pb                   : 市净率（None 表示无数据）
    market_cap_billion   : 总市值（亿元）

    Returns
    -------
    float : 估值风险分 0-100
    """
    return _valuation_risk_score(pe_ttm, pb, market_cap_billion)
