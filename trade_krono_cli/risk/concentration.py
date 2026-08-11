"""
集中度风险模块 — Concentration Risk。

当前版本基于占位实现，未来可接入组合权重数据进行真实集中度计算。
"""
from __future__ import annotations

from typing import Optional


def calc_concentration_risk(ta_result=None) -> float:
    """
    计算集中度风险分（0-100）。

    当前逻辑：
      - 暂不支持真实组合权重，返回默认中等风险（10 分）
      - 未来可接入持仓数据，按个股占组合比例映射风险分

    Parameters
    ----------
    ta_result : StockAnalysisResult or None
        若有 TA 结果，可从其 reports 中提取行业/估值线索

    Returns
    -------
    risk_score : 0-100
    """
    # TODO: 接入真实组合权重数据后替换此占位逻辑
    # 当前：无组合数据时返回默认值
    return 10.0
