"""
配置统一入口。

所有子配置模块从 trade_krono_cli.configs.* 导入，
旧路径 trade_krono_cli.configs.schema 通过兼容性层保留。
"""

from __future__ import annotations

from trade_krono_cli.configs.abnormality import AbnormalityConfig
from trade_krono_cli.configs.degradation import DegradationConfig
from trade_krono_cli.configs.filters import FilterConfig

# ── 新模块（推荐）──────────────────────────────────────────────────────────────
from trade_krono_cli.configs.kronos import KronosConfig
from trade_krono_cli.configs.logging import LoggingConfig
from trade_krono_cli.configs.output import OutputConfig
from trade_krono_cli.configs.retry import RetryConfig
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
from trade_krono_cli.configs.ta import TAConfig
from trade_krono_cli.configs.trading import ConstraintConfig

__all__ = [
    "KronosConfig",
    "TAConfig",
    "ScoringConfig",
    "ScoringStrategyConfig",
    "RiskBoostStrategyConfig",
    "RiskConfig",
    "RiskWeights",
    "VolatilityThresholds",
    "DrawdownThresholds",
    "LiquidityThresholds",
    "MarketRegimeThresholds",
    "FilterConfig",
    "AbnormalityConfig",
    "ConstraintConfig",
    "OutputConfig",
    "LoggingConfig",
    "RetryConfig",
    "DegradationConfig",
]
