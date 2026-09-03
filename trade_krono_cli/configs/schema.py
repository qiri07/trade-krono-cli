"""兼容性层 — 从新子配置模块重新导出旧名称。

所有业务逻辑已迁移至 trade_krono_cli.configs.* 子模块。
此文件仅保留向后兼容，新代码应直接导入子模块。
"""

from __future__ import annotations

from trade_krono_cli.configs.risk import (
    DrawdownThresholds,
    LiquidityThresholds,
    MarketRegimeThresholds,
    RiskConfig,
    RiskWeights,
    VolatilityThresholds,
)
from trade_krono_cli.configs.scoring import (
    RiskBoostStrategyConfig,
    ScoringConfig,
    ScoringStrategyConfig,
)
from trade_krono_cli.configs.trading import (
    ConstraintConfig,
)

__all__ = [
    "ConstraintConfig",
    "DrawdownThresholds",
    "LiquidityThresholds",
    "MarketRegimeThresholds",
    "RiskBoostStrategyConfig",
    "RiskConfig",
    "RiskWeights",
    "ScoringConfig",
    "ScoringStrategyConfig",
    "VolatilityThresholds",
]
