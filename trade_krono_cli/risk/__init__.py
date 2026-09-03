"""风险引擎 v2 — Risk Engine。

多维度风险量化，输出 RiskMetrics（VaR/CVaR/Beta/Gap/Event/Valuation）
+ 向后兼容的 RiskScore（0-100 综合分）。

输出示例：
  RiskMetrics for sh.600519 (2026-08-11)
  ======================================
    VaR(95%)        -2.34%
    CVaR(95%)       -3.12%
    Beta            1.15
    Ann. Volatility 32.5%
    Max Drawdown   -18.3%
    Gap Risk         25
    Event Risk       42
    Valuation Risk   30
    Liquidity Risk   12
    Market Regime    28
  --------------------------------------
    Total Risk      45.2
    Return Adj      -0.062  (预期收益降低 6.2%)
"""

from __future__ import annotations

from trade_krono_cli.risk.risk_engine import (
    RiskEngine,
    RiskMetrics,
    RiskScore,
    assess_risk,
)

__all__ = [
    "RiskEngine",
    "RiskMetrics",
    "RiskScore",
    "assess_risk",
]
