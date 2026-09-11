"""集中度风险模块 — Concentration Risk。

计算逻辑：
  · 无组合权重数据时，使用启发式规则（行业分散度、市值规模）估算
  · 有行业信息时，低流通行业 → 较高集中度风险分
  · 大盘蓝筹（市值大）→ 较低集中度风险分
  · 无足够信息时，返回保守默认值（15 分）

未来可接入真实组合权重后替换为精确计算。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trade_krono_cli.ta_runner import StockAnalysisResult

# 行业集中度风险系数（流通性差/集中度高的行业风险更高）
_SECTOR_CONCENTRATION_RISK: dict[str, float] = {
    "bank": 8.0,
    "insurance": 10.0,
    " Securities": 12.0,
    "real_estate": 15.0,
    "energy": 12.0,
    "materials": 14.0,
    "tech": 10.0,
    "healthcare": 8.0,
    "consumer": 7.0,
    "transport": 9.0,
    "telecom": 8.0,
}

# 默认基础风险分（无行业信息时）
_DEFAULT_CONCENTRATION_SCORE = 15.0


def calc_concentration_risk(ta_result: "StockAnalysisResult | None" = None) -> float:
    """计算集中度风险分（0-100）。

    采用启发式规则：
      1. 无 TA 结果 → 默认中等风险（15 分）
      2. 有行业信息 → 按行业集中度系数调整
      3. 市值大（≥500亿）→ 适度降低风险（大盘蓝筹流动性好）

    Parameters
    ----------
    ta_result : StockAnalysisResult or None
        TA 分析结果，可从其 reports 中提取行业/估值线索

    Returns
    -------
    risk_score : float
        0-100 的集中度风险分，越低表示风险越小

    """
    if ta_result is None:
        return _DEFAULT_CONCENTRATION_SCORE

    # 从 reports 提取行业信息
    reports = getattr(ta_result, "reports", None) or {}
    sector = _extract_sector(reports)

    base_score = _DEFAULT_CONCENTRATION_SCORE

    if sector:
        sector_key = sector.lower().strip()
        risk_factor = _SECTOR_CONCENTRATION_RISK.get(sector_key, 12.0)
        base_score = risk_factor

    # 市值调整：大盘蓝筹集中度风险更低
    market_cap = _extract_market_cap(reports)
    if market_cap is not None and market_cap >= 500:  # 单位：亿元
        base_score = max(2.0, base_score - 5.0)
    elif market_cap is not None and market_cap < 50:  # 小盘股流动性差
        base_score = min(60.0, base_score + 8.0)

    return round(base_score, 1)


def _extract_sector(reports: dict) -> str | None:
    """从 TA reports 中提取行业信息。"""
    # 优先从 fundamental_report 或 industry_report 中提取
    for key in ("fundamental_report", "industry_report", "analysis_report"):
        report = reports.get(key)
        if report and isinstance(report, str):
            # 简单关键词匹配：寻找行业相关关键词
            lower = report.lower()
            for sector_name in _SECTOR_CONCENTRATION_RISK:
                if sector_name in lower:
                    return sector_name
            # 进一步尝试常见行业中文关键词
            cn_keywords = {
                "银行": "bank",
                "保险": "insurance",
                "证券": "Securities",
                "房地产": "real_estate",
                "能源": "energy",
                "材料": "materials",
                "科技": "tech",
                "医疗": "healthcare",
                "消费": "consumer",
                "交通": "transport",
                "通信": "telecom",
            }
            for cn, en in cn_keywords.items():
                if cn in report:
                    return en
    return None


def _extract_market_cap(reports: dict) -> float | None:
    """从 TA reports 中提取市值（亿元）。"""
    for key in ("fundamental_report", "analysis_report"):
        report = reports.get(key)
        if report and isinstance(report, str):
            # 查找 "市值.*?([0-9.]+)" 或 "market_cap.*?([0-9.]+)" 模式
            import re

            patterns = [
                r"市值[：:\s]*([0-9.]+)\s*亿",
                r"market[_\s]?cap[：:\s]*([0-9.]+)\s*(?:亿|billion)?",
                r"总市值[：:\s]*([0-9.]+)",
            ]
            for pat in patterns:
                m = re.search(pat, report, re.IGNORECASE)
                if m:
                    try:
                        return float(m.group(1))
                    except (ValueError, IndexError):
                        continue
    return None
