"""
兼容性层 — 从新子配置模块重新导出旧名称。

所有业务逻辑已迁移至 trade_krono_cli.configs.* 子模块。
此文件仅保留向后兼容，新代码应直接导入子模块。
"""
from __future__ import annotations

from trade_krono_cli.configs.scoring import (
    ScoringConfig as ScoringConfig,
    ScoringStrategyConfig as ScoringStrategyConfig,
    RiskBoostStrategyConfig as RiskBoostStrategyConfig,
)
from trade_krono_cli.configs.risk import (
    RiskConfig as RiskConfig,
    RiskWeights as RiskWeights,
    VolatilityThresholds as VolatilityThresholds,
    DrawdownThresholds as DrawdownThresholds,
    LiquidityThresholds as LiquidityThresholds,
    MarketRegimeThresholds as MarketRegimeThresholds,
)
from trade_krono_cli.configs.trading import (
    ConstraintConfig as ConstraintConfig,
)

__all__ = [
    "ScoringConfig",
    "ScoringStrategyConfig",
    "RiskBoostStrategyConfig",
    "RiskConfig",
    "RiskWeights",
    "VolatilityThresholds",
    "DrawdownThresholds",
    "LiquidityThresholds",
    "MarketRegimeThresholds",
    "ConstraintConfig",
]
