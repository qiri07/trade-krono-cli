"""风险引擎配置。"""

from __future__ import annotations

from dataclasses import dataclass, field

# ── RiskWeights ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RiskWeights:
    """各风险维度的加权占比，总和应为 1.0。"""

    volatility: float = 0.25
    drawdown: float = 0.20
    liquidity: float = 0.15
    concentration: float = 0.08
    market_regime: float = 0.12
    gap_risk: float = 0.05
    event_risk: float = 0.05
    valuation_risk: float = 0.05
    beta: float = 0.05

    def validate(self) -> list[str]:
        errors: list[str] = []
        total = sum(self.__dict__.values())
        if not (0.99 <= total <= 1.01):
            errors.append(f"风险维度权重之和应为 ~1.0，当前={total:.3f}")
        for name, w in self.__dict__.items():
            if w < 0:
                errors.append(f"风险权重 {name}={w} 不能为负")
        return errors

    def merge(self, **overrides) -> "RiskWeights":
        current = {k: getattr(self, k) for k in self.__dataclass_fields__}
        current.update({k: v for k, v in overrides.items() if v is not None})
        return RiskWeights(**current)


# ── Threshold types ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class VolatilityThresholds:
    """波动率 → 风险分 分段映射。"""

    low_pct: float = 0.0
    high_pct: float = 60.0
    insufficient_data_score: float = 25.0
    insufficient_data_min_rows: int = 30


@dataclass(frozen=True)
class DrawdownThresholds:
    """最大回撤 → 风险分 分段映射。"""

    breakpoints: list[tuple[float, float]] = field(
        default_factory=lambda: [(5.0, 20.0), (20.0, 60.0), (40.0, 100.0)]
    )
    insufficient_data_score: float = 20.0
    insufficient_data_min_rows: int = 30

    def validate(self) -> list[str]:
        errors: list[str] = []
        if len(self.breakpoints) != 3:
            errors.append(f"drawdown breakpoints 应为 3 个点，当前={len(self.breakpoints)}")
        else:
            if self.breakpoints[0][0] >= self.breakpoints[1][0]:
                errors.append("drawdown breakpoints 左端点须递增")
            if self.breakpoints[1][0] >= self.breakpoints[2][0]:
                errors.append("drawdown breakpoints 左端点须递增")
            if self.breakpoints[0][1] >= self.breakpoints[1][1]:
                errors.append("drawdown breakpoints 右端点须递增")
            if self.breakpoints[1][1] >= self.breakpoints[2][1]:
                errors.append("drawdown breakpoints 右端点须递增")
        return errors


@dataclass(frozen=True)
class LiquidityThresholds:
    """成交量 → 风险分 分段映射（log 空间）。"""

    breakpoints: list[tuple[float, float]] = field(
        default_factory=lambda: [
            (5.0, 80.0),
            (6.0, 60.0),
            (7.0, 40.0),
            (8.0, 20.0),
        ]
    )
    tail_penalty_rate: float = 5.0
    insufficient_data_score: float = 30.0
    insufficient_data_min_rows: int = 10


@dataclass(frozen=True)
class MarketRegimeThresholds:
    """动量 → 风险分 分段映射。"""

    bear_threshold: float = -10.0
    neutral_low: float = 0.0
    neutral_high: float = 10.0
    bear_score: float = 80.0
    neutral_mid_score: float = 50.0
    bull_base_score: float = 20.0
    insufficient_data_score: float = 30.0
    insufficient_data_min_rows: int = 30

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not (self.bear_threshold < self.neutral_low < self.neutral_high):
            errors.append("market_regime 阈值须满足: bear_threshold < neutral_low < neutral_high")
        return errors


@dataclass(frozen=True)
class GapRiskThresholds:
    """缺口风险阈值。"""

    min_gap_pct: float = 3.0
    insufficient_data_min_rows: int = 30
    insufficient_data_score: float = 50.0


@dataclass(frozen=True)
class EventRiskThresholds:
    """事件风险阈值。"""

    short_window: int = 10
    long_window: int = 60
    insufficient_data_min_rows: int = 60
    insufficient_data_score: float = 50.0


@dataclass(frozen=True)
class ValuationRiskThresholds:
    """估值风险阈值。"""

    pe_high: float = 100.0
    pe_low: float = 10.0
    pb_high: float = 5.0
    pb_low: float = 0.5
    small_cap_threshold: float = 20.0  # 亿元


# ── RiskConfig ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RiskConfig:
    """风险引擎全量配置。"""

    weights: RiskWeights = field(default_factory=RiskWeights)
    volatility: VolatilityThresholds = field(default_factory=VolatilityThresholds)
    drawdown: DrawdownThresholds = field(default_factory=DrawdownThresholds)
    liquidity: LiquidityThresholds = field(default_factory=LiquidityThresholds)
    market_regime: MarketRegimeThresholds = field(default_factory=MarketRegimeThresholds)
    gap_risk: GapRiskThresholds = field(default_factory=GapRiskThresholds)
    event_risk: EventRiskThresholds = field(default_factory=EventRiskThresholds)
    valuation_risk: ValuationRiskThresholds = field(default_factory=ValuationRiskThresholds)
    beta_default: float = 1.0
    var_confidence: float = 0.95
    var_lookback: int = 60
    enable_cost_model: bool = True
    commission_bps: float = 3.0
    slippage_bps: float = 5.0
    stamp_duty_bps: float = 1.0

    def validate(self) -> list[str]:
        errors: list[str] = []
        errors.extend(self.weights.validate())
        errors.extend(self.drawdown.validate())
        errors.extend(self.market_regime.validate())
        if not (0.0 <= self.var_confidence < 1.0):
            errors.append(f"var_confidence={self.var_confidence} 须在 (0, 1) 范围内")
        for name, val in [
            ("commission_bps", self.commission_bps),
            ("slippage_bps", self.slippage_bps),
            ("stamp_duty_bps", self.stamp_duty_bps),
        ]:
            if val < 0:
                errors.append(f"{name}={val} 不能为负")
        return errors

    def merge(self, **overrides) -> "RiskConfig":
        """支持嵌套覆盖：merge(weights__volatility=0.35)。"""
        nested: dict[str, dict] = {}
        flat: dict = {}
        for k, v in overrides.items():
            if "__" in k:
                outer, inner = k.split("__", 1)
                nested.setdefault(outer, {})[inner] = v
            else:
                flat[k] = v
        current = {k: getattr(self, k) for k in self.__dataclass_fields__}
        current.update(flat)
        for outer, inner_overrides in nested.items():
            if outer in current and hasattr(current[outer], "merge"):
                current[outer] = current[outer].merge(**inner_overrides)
            else:
                current[outer] = inner_overrides
        return RiskConfig(**current)
