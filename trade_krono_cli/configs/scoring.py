"""综合打分配置。"""

from __future__ import annotations

from dataclasses import dataclass, field

# ── ScoringConfig ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ScoringConfig:
    """综合打分（满分 100）各子项权重与分段映射参数。"""

    ta_confidence_weight: float = 0.40
    change_pct_weight: float = 0.30
    direction_base_weight: float = 0.10
    uncertainty_base_weight: float = 0.10
    risk_penalty_weight: float = 0.15

    direction_bonus_point: float = 10.0
    change_pct_offset: float = 50.0

    uncertainty_high_threshold: float = 70.0
    uncertainty_med_threshold: float = 50.0
    uncertainty_high_bonus: float = 3.0
    uncertainty_med_bonus: float = 1.0
    uncertainty_low_penalty: float = -2.0

    def validate(self) -> list[str]:
        errors: list[str] = []
        total_weight = (
            self.ta_confidence_weight
            + self.change_pct_weight
            + self.direction_base_weight
            + self.uncertainty_base_weight
            + self.risk_penalty_weight
        )
        if not (0 < total_weight <= 1.0):
            errors.append(f"打分权重之和应为 (0, 1.0]，当前={total_weight:.3f}")
        if not (0 <= self.uncertainty_high_threshold <= 100):
            errors.append(
                f"uncertainty_high_threshold 应在 [0, 100]，当前={self.uncertainty_high_threshold}",
            )
        if not (0 <= self.uncertainty_med_threshold <= 100):
            errors.append(
                f"uncertainty_med_threshold 应在 [0, 100]，当前={self.uncertainty_med_threshold}",
            )
        if self.uncertainty_med_threshold >= self.uncertainty_high_threshold:
            errors.append("uncertainty_med_threshold 必须 < uncertainty_high_threshold")
        if self.change_pct_offset <= 0:
            errors.append(f"change_pct_offset={self.change_pct_offset} 必须 > 0")
        return errors

    def merge(self, **overrides) -> ScoringConfig:
        current = {
            "ta_confidence_weight": self.ta_confidence_weight,
            "change_pct_weight": self.change_pct_weight,
            "direction_base_weight": self.direction_base_weight,
            "uncertainty_base_weight": self.uncertainty_base_weight,
            "risk_penalty_weight": self.risk_penalty_weight,
            "direction_bonus_point": self.direction_bonus_point,
            "change_pct_offset": self.change_pct_offset,
            "uncertainty_high_threshold": self.uncertainty_high_threshold,
            "uncertainty_med_threshold": self.uncertainty_med_threshold,
            "uncertainty_high_bonus": self.uncertainty_high_bonus,
            "uncertainty_med_bonus": self.uncertainty_med_bonus,
            "uncertainty_low_penalty": self.uncertainty_low_penalty,
        }
        current.update({k: v for k, v in overrides.items() if v is not None})
        return ScoringConfig(**current)


# ── ScoringStrategyConfig ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class ScoringStrategyConfig:
    """综合打分策略配置。"""

    strategy: str = "linear"
    params: dict = field(default_factory=dict)

    def validate(self) -> list[str]:
        errors: list[str] = []
        valid_strategies = {"linear", "multiplicative", "rank_based"}
        if self.strategy not in valid_strategies:
            errors.append(
                f"SCORING_STRATEGY={self.strategy} 必须是以下之一: "
                f"{', '.join(sorted(valid_strategies))}",
            )
        return errors

    def merge(self, **overrides) -> ScoringStrategyConfig:
        current: dict[str, object] = {"strategy": self.strategy, "params": dict(self.params)}
        current.update(overrides)
        return ScoringStrategyConfig(  # type: ignore[call-arg]
            strategy=str(current["strategy"]),
            params=dict(current["params"]) if isinstance(current["params"], dict) else {},
        )


# ── RiskBoostStrategyConfig ───────────────────────────────────────────────────


@dataclass(frozen=True)
class RiskBoostStrategyConfig:
    """异常标记风险加分策略配置。"""

    strategy: str = "fixed_boost"
    multiplier: float = 1.0
    diminishing_power: float = 0.5

    def validate(self) -> list[str]:
        errors: list[str] = []
        valid = {"fixed_boost", "scaled_boost", "diminishing_boost"}
        if self.strategy not in valid:
            errors.append(
                f"RISK_BOOST_STRATEGY={self.strategy} 必须是以下之一: {', '.join(sorted(valid))}",
            )
        if not (0 < self.multiplier <= 5.0):
            errors.append(f"RISK_BOOST_MULTIPLIER={self.multiplier} 必须在 (0, 5.0] 范围内")
        if not (0 < self.diminishing_power <= 1.0):
            errors.append(
                f"RISK_BOOST_DIMINISHING_POWER={self.diminishing_power} 必须在 (0, 1.0] 范围内",
            )
        return errors

    def merge(self, **overrides) -> RiskBoostStrategyConfig:
        current: dict[str, object] = {
            "strategy": self.strategy,
            "multiplier": self.multiplier,
            "diminishing_power": self.diminishing_power,
        }
        current.update(overrides)
        return RiskBoostStrategyConfig(  # type: ignore[call-arg]
            strategy=str(current["strategy"]),
            multiplier=float(current["multiplier"])
            if isinstance(current["multiplier"], (int, float))
            else self.multiplier,
            diminishing_power=float(current["diminishing_power"])
            if isinstance(current["diminishing_power"], (int, float))
            else self.diminishing_power,
        )
