"""
配置 Schema — 集中管理所有可调参数。

将散落在各模块的硬编码阈值、权重、分段映射统一到此，
支持从 pipeline_config.yaml 加载覆盖。

参数优先级（高 → 低）：
  1. CLI 命令行参数（typer.Option）
  2. 环境变量 / .env
  3. PipelineConfig（YAML/JSON 文件）
  4. 本模块定义的默认值
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ═══════════════════════════════════════════════════════
# 综合打分配置
# ═══════════════════════════════════════════════════════

@dataclass(frozen=True)
class ScoringConfig:
    """
    综合打分（满分 100）各子项权重与分段映射参数。

    默认值对应 hard-coded 常量：
      _TA_CONFIDENCE_WEIGHT        = 0.4
      _CHANGE_PCT_WEIGHT           = 0.3
      _DIRECTION_BASE_WEIGHT       = 0.1
      _UNCERTAINTY_BASE_WEIGHT     = 0.1
      _RISK_PENALTY_WEIGHT         = 0.15
      _DIRECTION_BONUS_POINT       = 10
      _CHANGE_PCT_OFFSET           = 50
      _UNCERTAINTY_HIGH_THRESHOLD  = 70
      _UNCERTAINTY_MED_THRESHOLD   = 50
      _UNCERTAINTY_HIGH_BONUS      = 3.0
      _UNCERTAINTY_MED_BONUS       = 1.0
      _UNCERTAINTY_LOW_PENALTY     = -2.0
    """

    # ── 权重（各子项在总分中的贡献比例）────────────────────
    ta_confidence_weight: float = 0.40
    change_pct_weight: float = 0.30
    direction_base_weight: float = 0.10
    uncertainty_base_weight: float = 0.10
    risk_penalty_weight: float = 0.15

    # ── 方向加成 ──────────────────────────────────────────
    direction_bonus_point: float = 10.0
    """方向加成乘数：_DIRECTION_BASE_WEIGHT × _DIRECTION_BONUS_POINT = ±1 分"""

    # ── 涨跌幅映射 ────────────────────────────────────────
    change_pct_offset: float = 50.0
    """将 [-change_pct_offset, +change_pct_offset] 线性映射到 [0, 100] 分的偏移量"""

    # ── 不确定性置信度分段阈值 ────────────────────────────
    uncertainty_high_threshold: float = 70.0
    uncertainty_med_threshold: float = 50.0
    uncertainty_high_bonus: float = 3.0
    uncertainty_med_bonus: float = 1.0
    uncertainty_low_penalty: float = -2.0

    # ── 验证 ──────────────────────────────────────────────
    def validate(self) -> list[str]:
        """返回错误消息列表（空 = 合法）。"""
        errors: list[str] = []
        total_weight = (
            self.ta_confidence_weight
            + self.change_pct_weight
            + self.direction_base_weight
            + self.uncertainty_base_weight
            + self.risk_penalty_weight
        )
        if not (0 < total_weight <= 1.0):
            errors.append(
                f"打分权重之和应为 (0, 1.0]，当前={total_weight:.3f}"
            )
        if not (0 <= self.uncertainty_high_threshold <= 100):
            errors.append(
                f"uncertainty_high_threshold 应在 [0, 100]，"
                f"当前={self.uncertainty_high_threshold}"
            )
        if not (0 <= self.uncertainty_med_threshold <= 100):
            errors.append(
                f"uncertainty_med_threshold 应在 [0, 100]，"
                f"当前={self.uncertainty_med_threshold}"
            )
        if self.uncertainty_med_threshold >= self.uncertainty_high_threshold:
            errors.append(
                "uncertainty_med_threshold 必须 < "
                "uncertainty_high_threshold"
            )
        if self.change_pct_offset <= 0:
            errors.append(
                f"change_pct_offset={self.change_pct_offset} 必须 > 0"
            )
        return errors

    def merge(self, **overrides) -> "ScoringConfig":
        """部分覆盖后返回新实例。"""
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


# ═══════════════════════════════════════════════════════
# 评分策略配置
# ═══════════════════════════════════════════════════════

@dataclass(frozen=True)
class ScoringStrategyConfig:
    """
    综合打分策略配置。

    参数：
      strategy : 策略名称
        - "linear"       : 加权线性组合（默认）
        - "multiplicative": 乘法衰减型（高风险→分数压缩）
        - "rank_based"    : 百分位排名转换
      params : 策略特定参数（JSON 序列化的 dict）
    """
    strategy: str = "linear"
    params: dict = field(default_factory=dict)

    def validate(self) -> list[str]:
        errors: list[str] = []
        valid_strategies = {"linear", "multiplicative", "rank_based"}
        if self.strategy not in valid_strategies:
            errors.append(
                f"SCORING_STRATEGY={self.strategy} 必须是以下之一: "
                f"{', '.join(sorted(valid_strategies))}"
            )
        return errors

    def merge(self, **overrides) -> "ScoringStrategyConfig":
        current = {"strategy": self.strategy, "params": dict(self.params)}
        current.update(overrides)
        return ScoringStrategyConfig(**current)


@dataclass(frozen=True)
class RiskBoostStrategyConfig:
    """
    异常标记风险加分策略配置。

    参数：
      strategy : 策略名称
        - "fixed_boost"       : 固定值叠加（默认）
        - "scaled_boost"      : 按比例缩放
        - "diminishing_boost" : 边际递减（√n 缩放）
      multiplier     : scaled_boost 倍率，默认 1.0
      diminishing_power : diminishing_boost 幂次，默认 0.5（即 √n）
    """
    strategy: str = "fixed_boost"
    multiplier: float = 1.0
    diminishing_power: float = 0.5

    def validate(self) -> list[str]:
        errors: list[str] = []
        valid = {"fixed_boost", "scaled_boost", "diminishing_boost"}
        if self.strategy not in valid:
            errors.append(
                f"RISK_BOOST_STRATEGY={self.strategy} 必须是以下之一: "
                f"{', '.join(sorted(valid))}"
            )
        if not (0 < self.multiplier <= 5.0):
            errors.append(
                f"RISK_BOOST_MULTIPLIER={self.multiplier} 必须在 (0, 5.0] 范围内"
            )
        if not (0 < self.diminishing_power <= 1.0):
            errors.append(
                f"RISK_BOOST_DIMINISHING_POWER={self.diminishing_power} 必须在 (0, 1.0] 范围内"
            )
        return errors

    def merge(self, **overrides) -> "RiskBoostStrategyConfig":
        current = {
            "strategy": self.strategy,
            "multiplier": self.multiplier,
            "diminishing_power": self.diminishing_power,
        }
        current.update(overrides)
        return RiskBoostStrategyConfig(**current)


# ═══════════════════════════════════════════════════════
# 风险引擎配置
# ═══════════════════════════════════════════════════════

@dataclass(frozen=True)
class RiskWeights:
    """各风险维度的加权占比，总和应为 1.0。"""

    volatility: float = 0.30
    drawdown: float = 0.25
    liquidity: float = 0.20
    concentration: float = 0.10
    market_regime: float = 0.15

    def validate(self) -> list[str]:
        errors: list[str] = []
        total = sum(self.__dict__.values())
        if not (0.99 <= total <= 1.01):
            errors.append(
                f"风险维度权重之和应为 ~1.0，当前={total:.3f}"
            )
        for name, w in self.__dict__.items():
            if w < 0:
                errors.append(f"风险权重 {name}={w} 不能为负")
        return errors

    def merge(self, **overrides) -> "RiskWeights":
        current = {k: getattr(self, k) for k in self.__dict__}
        current.update({k: v for k, v in overrides.items() if v is not None})
        return RiskWeights(**current)


@dataclass(frozen=True)
class VolatilityThresholds:
    """
    波动率 → 风险分 分段映射。
    默认：vol 0%→0分，vol 60%→100分（线性）。
    """
    low_pct: float = 0.0       # 风险分 0%
    high_pct: float = 60.0     # 风险分 100%
    insufficient_data_score: float = 25.0
    insufficient_data_min_rows: int = 30


@dataclass(frozen=True)
class DrawdownThresholds:
    """
    最大回撤 → 风险分 分段映射。
    默认：-5%→20分，-20%→60分，-40%→100分。
    """
    # (abs_drawdown, risk_score) 三个分段点
    breakpoints: list[tuple[float, float]] = field(
        default_factory=lambda: [(5.0, 20.0), (20.0, 60.0), (40.0, 100.0)]
    )
    insufficient_data_score: float = 20.0
    insufficient_data_min_rows: int = 30

    def validate(self) -> list[str]:
        errors: list[str] = []
        if len(self.breakpoints) != 3:
            errors.append(
                f"drawdown breakpoints 应为 3 个点，当前={len(self.breakpoints)}"
            )
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
    """
    成交量 → 风险分 分段映射（log 空间）。
    默认：log1p(vol)<5→80分，<6→60分，<7→40分，<8→20分，>=8→递减。
    """
    breakpoints: list[tuple[float, float]] = field(
        default_factory=lambda: [
            (5.0, 80.0),
            (6.0, 60.0),
            (7.0, 40.0),
            (8.0, 20.0),
        ]
    )
    tail_penalty_rate: float = 5.0    # log_vol > 8 后每增加 1 扣减分数
    insufficient_data_score: float = 30.0
    insufficient_data_min_rows: int = 10


@dataclass(frozen=True)
class MarketRegimeThresholds:
    """
    动量 → 风险分 分段映射。
    默认：<=-10%→80分，<=0%→50-80分，<=10%→20-50分，>10%→0-20分。
    """
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
            errors.append(
                "market_regime 阈值须满足: "
                "bear_threshold < neutral_low < neutral_high"
            )
        return errors


@dataclass(frozen=True)
class RiskConfig:
    """风险引擎全量配置。"""
    weights: RiskWeights = field(default_factory=RiskWeights)
    volatility: VolatilityThresholds = field(default_factory=VolatilityThresholds)
    drawdown: DrawdownThresholds = field(default_factory=DrawdownThresholds)
    liquidity: LiquidityThresholds = field(default_factory=LiquidityThresholds)
    market_regime: MarketRegimeThresholds = field(
        default_factory=MarketRegimeThresholds
    )
    enable_cost_model: bool = True
    commission_bps: float = 3.0
    slippage_bps: float = 5.0
    stamp_duty_bps: float = 1.0

    def validate(self) -> list[str]:
        errors: list[str] = []
        errors.extend(self.weights.validate())
        errors.extend(self.drawdown.validate())
        errors.extend(self.market_regime.validate())
        for name, val in [
            ("commission_bps", self.commission_bps),
            ("slippage_bps", self.slippage_bps),
            ("stamp_duty_bps", self.stamp_duty_bps),
        ]:
            if val < 0:
                errors.append(f"{name}={val} 不能为负")
        return errors

    def merge(self, **overrides) -> "RiskConfig":
        """
        支持嵌套覆盖：
          merge(weights__volatility=0.35)
          merge(drawdown__breakpoints=[(5, 20), (20, 60), (40, 100)])
        """
        nested = {}
        flat = {}
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


# ═══════════════════════════════════════════════════════
# 约束配置
# ═══════════════════════════════════════════════════════

@dataclass(frozen=True)
class ConstraintConfig:
    """A 股交易约束参数。"""
    enable_limit_check: bool = True
    sse_limit_pct: float = 10.0
    szse_limit_pct: float = 20.0
    enable_t1: bool = True
    enable_st_filter: bool = True

    def merge(self, **overrides) -> "ConstraintConfig":
        current = {k: getattr(self, k) for k in self.__dataclass_fields__}
        current.update(overrides)
        return ConstraintConfig(**current)
