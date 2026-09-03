"""scoring — 评分与风险引擎插件系统。

提供可插拔的打分策略和风险加分策略，
支持通过配置切换不同策略，便于快速实验和 A/B 评估。

使用方式：
    # 获取打分策略
    from trade_krono_cli.scoring import get_scorer_registry
    scorer = get_scorer_registry().get("linear")
    score = scorer.score(merged_result, scoring_config)

    # 获取风险加分策略
    from trade_krono_cli.scoring import get_risk_boost_registry
    booster = get_risk_boost_registry().get("fixed_boost")
    boosted = booster.boost(base_risk, flags, params)

    # 向后兼容
    from trade_krono_cli.scoring import apply_abnormality_risk_boost
    new_risk = apply_abnormality_risk_boost(40.0, ["ST"], strategy="scaled_boost", params={"multiplier": 0.5})
"""

from __future__ import annotations

from trade_krono_cli.scoring.base import (
    BoostResult,
    CompositeScorer,
    RatingMapper,
    RiskBoostStrategy,
    ScoreResult,
)
from trade_krono_cli.scoring.registry import (
    RiskBoostRegistry,
    ScorerRegistry,
    get_risk_boost_registry,
    get_scorer_registry,
    reset_scoring_registries,
)
from trade_krono_cli.scoring.risk_boosters import (
    DiminishingBoostBooster,
    FixedBoostBooster,
    ScaledBoostBooster,
    apply_abnormality_risk_boost,
)
from trade_krono_cli.scoring.scorers import (
    LinearScorer,
    MultiplicativeScorer,
    RankBasedScorer,
)

__all__ = [
    "BoostResult",
    # ABC
    "CompositeScorer",
    "DiminishingBoostBooster",
    # Risk Boosters
    "FixedBoostBooster",
    # Scorers
    "LinearScorer",
    "MultiplicativeScorer",
    "RankBasedScorer",
    "RatingMapper",
    "RiskBoostRegistry",
    "RiskBoostStrategy",
    "ScaledBoostBooster",
    "ScoreResult",
    # Registry
    "ScorerRegistry",
    "apply_abnormality_risk_boost",
    "get_risk_boost_registry",
    "get_scorer_registry",
    "reset_scoring_registries",
]
