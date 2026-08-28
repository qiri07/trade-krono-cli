"""
兼容性层 — 从新子配置模块重新导出旧名称。

所有业务逻辑已迁移至 trade_krono_cli.configs.* 子模块。
此文件仅保留向后兼容，新代码应直接导入子模块。
"""

from __future__ import annotations

from trade_krono_cli.configs.risk import (
    DrawdownThresholds as DrawdownThresholds,
)
from trade_krono_cli.configs.risk import (
    LiquidityThresholds as LiquidityThresholds,
)
from trade_krono_cli.configs.risk import (
    MarketRegimeThresholds as MarketRegimeThresholds,
)
from trade_krono_cli.configs.risk import (
    RiskConfig as RiskConfig,
)
from trade_krono_cli.configs.risk import (
    RiskWeights as RiskWeights,
)
from trade_krono_cli.configs.risk import (
    VolatilityThresholds as VolatilityThresholds,
)
from trade_krono_cli.configs.scoring import (
    RiskBoostStrategyConfig as RiskBoostStrategyConfig,
)
from trade_krono_cli.configs.scoring import (
    ScoringConfig as ScoringConfig,
)
from trade_krono_cli.configs.scoring import (
    ScoringStrategyConfig as ScoringStrategyConfig,
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
