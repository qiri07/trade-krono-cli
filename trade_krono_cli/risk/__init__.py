"""
风险引擎 — Risk Engine。

多维度风险量化，输出 0-100 综合风险分（越高越危险）。

风险维度：
  volatility       波动率风险（基于 K 线日收益率标准差）
  drawdown         回撤风险（基于最大回撤）
  liquidity        流动性风险（基于日均成交量/换手率）
  concentration    集中度风险（预留接口，当前返回默认值）
  market_regime    市场环境风险（基于市场趋势动量）

总风险分 = Σ(各维度分 × 权重)

集成至 merge.py：高风险股票综合评分向下修正（最高扣 15 分）。
"""
from __future__ import annotations

from trade_krono_cli.risk.risk_engine import (
    RiskEngine,
    RiskScore,
    assess_risk,
)

__all__ = [
    "RiskEngine",
    "RiskScore",
    "assess_risk",
]
