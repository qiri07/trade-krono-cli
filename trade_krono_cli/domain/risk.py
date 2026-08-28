"""
Risk — 风险评估领域对象。

RiskAssessment 整合多源风险因子（波动率 / 流动性 / 估值 / 事件 / 市场状态），
输出综合风险评分和风险调整后的预期收益。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class RiskFactor:
    """单个风险因子的评估结果。"""

    name: str  # 因子名称（如 "volatility", "liquidity"）
    score: float  # 风险评分 0–100（越高越危险）
    weight: float = 1.0  # 在综合评分中的权重
    detail: str = ""  # 人类可读描述

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "score": self.score,
            "weight": self.weight,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class RiskAssessment:
    """
    综合风险评估结果。

    Parameters
    ----------
    ticker                  股票代码
    eval_date               评估日期
    risk_score_total        综合风险评分 0–100
    volatility_score        波动率风险评分
    liquidity_score         流动性风险评分
    valuation_score         估值风险评分
    event_risk_score        事件风险评分
    market_regime_score     市场状态风险评分
    gap_risk_score          跳空风险评分
    concentration_score     集中度风险评分

    adjusted_expected_return 风险调整后的预期收益（%）
                             由风险引擎计算：原收益 − 风险惩罚
    risk_factors            各子因子详情列表

    VaR / CVaR
    ----------
    var_95                  95% 置信度 VaR（%）
    cvar_95                 95% 置信度 CVaR（%）
    max_drawdown_pct        最大回撤（%）
    """

    ticker: str
    eval_date: str
    risk_score_total: float = 0.0

    # 子因子评分
    volatility_score: float = 0.0
    liquidity_score: float = 0.0
    valuation_score: float = 0.0
    event_risk_score: float = 0.0
    market_regime_score: float = 0.0
    gap_risk_score: float = 0.0
    concentration_score: float = 0.0

    # 风险调整后收益
    adjusted_expected_return: Optional[float] = None

    # 风险因子的详细描述
    risk_factors: list[RiskFactor] = field(default_factory=list)

    # VaR / CVaR
    var_95: Optional[float] = None
    cvar_95: Optional[float] = None
    max_drawdown_pct: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "eval_date": self.eval_date,
            "risk_score_total": self.risk_score_total,
            "volatility_score": self.volatility_score,
            "liquidity_score": self.liquidity_score,
            "valuation_score": self.valuation_score,
            "event_risk_score": self.event_risk_score,
            "market_regime_score": self.market_regime_score,
            "gap_risk_score": self.gap_risk_score,
            "concentration_score": self.concentration_score,
            "adjusted_expected_return": self.adjusted_expected_return,
            "risk_factors": [f.to_dict() for f in self.risk_factors],
            "var_95": self.var_95,
            "cvar_95": self.cvar_95,
            "max_drawdown_pct": self.max_drawdown_pct,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RiskAssessment":
        factors = [RiskFactor(**f) for f in data.get("risk_factors", [])]
        return cls(
            ticker=data["ticker"],
            eval_date=data.get("eval_date", ""),
            risk_score_total=float(data.get("risk_score_total", 0.0)),
            volatility_score=float(data.get("volatility_score", 0.0)),
            liquidity_score=float(data.get("liquidity_score", 0.0)),
            valuation_score=float(data.get("valuation_score", 0.0)),
            event_risk_score=float(data.get("event_risk_score", 0.0)),
            market_regime_score=float(data.get("market_regime_score", 0.0)),
            gap_risk_score=float(data.get("gap_risk_score", 0.0)),
            concentration_score=float(data.get("concentration_score", 0.0)),
            adjusted_expected_return=data.get("adjusted_expected_return"),
            risk_factors=factors,
            var_95=data.get("var_95"),
            cvar_95=data.get("cvar_95"),
            max_drawdown_pct=data.get("max_drawdown_pct"),
        )

    @classmethod
    def empty(cls, ticker: str, eval_date: str) -> "RiskAssessment":
        return cls(ticker=ticker, eval_date=eval_date)
