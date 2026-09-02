"""
scoring.risk_boosters — 三种异常标记风险加分策略实现。

策略列表：
  fixed_boost        : 固定值叠加（默认，与原 apply_abnormality_risk_boost 等价）
  scaled_boost       : 按比例缩放（可配置倍率）
  diminishing_boost  : 边际递减（多 flag 叠加时平方根缩放，防止过度惩罚）
"""

from __future__ import annotations

from typing import Optional

from trade_krono_cli.abnormal_stock import (  # noqa: F401
    _RISK_BOOST_MAP,
    StockAbnormality,
    apply_abnormality_risk_boost,
)
from trade_krono_cli.scoring.base import BoostResult, RiskBoostStrategy

# ═══════════════════════════════════════════════════════
# FixedBoostBooster（默认）
# ═══════════════════════════════════════════════════════


class FixedBoostBooster(RiskBoostStrategy):
    """
    固定值叠加型风险加分。

    每个异常标记有固定加分值（来自 _RISK_BOOST_MAP），
    累加后 capped 到 100。与原逻辑完全一致。
    """

    name = "fixed_boost"

    def _boost_impl(
        self, base_risk: float, flags: list[str], params: Optional[dict] = None
    ) -> BoostResult:
        multiplier = (params or {}).get("multiplier", 1.0)
        total_boost = 0.0
        applied: list[str] = []

        for flag_str in flags:
            try:
                abnormality = StockAbnormality(flag_str)
                boost = _RISK_BOOST_MAP.get(abnormality, 0.0) * multiplier
                total_boost += boost
                applied.append(flag_str)
            except ValueError:
                pass  # 未知标记跳过

        boosted = min(100.0, base_risk + total_boost)
        return BoostResult(
            boosted_risk=round(boosted, 1),
            base_risk=base_risk,
            total_boost=round(total_boost, 1),
            flags_applied=applied,
        )


# ═══════════════════════════════════════════════════════
# ScaledBoostBooster（可配置倍率）
# ═══════════════════════════════════════════════════════


class ScaledBoostBooster(RiskBoostStrategy):
    """
    缩放型风险加分。

    在 fixed_boost 基础上增加全局倍率参数，
    可用于实验更温和（0.5×）或更激进（2.0×）的风险惩罚。
    """

    name = "scaled_boost"

    def _boost_impl(
        self, base_risk: float, flags: list[str], params: Optional[dict] = None
    ) -> BoostResult:
        multiplier = (params or {}).get("multiplier", 1.0)
        total_boost = 0.0
        applied: list[str] = []

        for flag_str in flags:
            try:
                abnormality = StockAbnormality(flag_str)
                boost = _RISK_BOOST_MAP.get(abnormality, 0.0) * multiplier
                total_boost += boost
                applied.append(flag_str)
            except ValueError:
                pass

        boosted = min(100.0, base_risk + total_boost)
        return BoostResult(
            boosted_risk=round(boosted, 1),
            base_risk=base_risk,
            total_boost=round(total_boost, 1),
            flags_applied=applied,
        )


# ═══════════════════════════════════════════════════════
# DiminishingBoostBooster（边际递减）
# ═══════════════════════════════════════════════════════


class DiminishingBoostBooster(RiskBoostStrategy):
    """
    边际递减型风险加分。

    多个异常标记叠加时，后续标记的加分按 √n 缩放递减，
    避免 ST+SUSPENDED+DELISTED 等组合导致风险分过高（>90）。

    公式：
      effective_boost = sum(boost_i) / n_flags ^ (1 - power)
      其中 power ∈ (0, 1]，默认 0.5（即 √n 缩放）
    """

    name = "diminishing_boost"

    def _boost_impl(
        self, base_risk: float, flags: list[str], params: Optional[dict] = None
    ) -> BoostResult:
        power = (params or {}).get("diminishing_power", 0.5)
        n_flags = len(flags)
        if n_flags == 0:
            return BoostResult(
                boosted_risk=base_risk, base_risk=base_risk, total_boost=0.0, flags_applied=[]
            )

        # 计算原始总加分
        raw_total = 0.0
        applied: list[str] = []
        for flag_str in flags:
            try:
                abnormality = StockAbnormality(flag_str)
                raw_total += _RISK_BOOST_MAP.get(abnormality, 0.0)
                applied.append(flag_str)
            except ValueError:
                pass

        # 边际递减：除以 n^(1-power)
        # power=0.5 → 除以 √n；power=1.0 → 不递减
        decay_factor = n_flags ** (1.0 - power)
        total_boost = raw_total / decay_factor

        boosted = min(100.0, base_risk + total_boost)
        return BoostResult(
            boosted_risk=round(boosted, 1),
            base_risk=base_risk,
            total_boost=round(total_boost, 1),
            flags_applied=applied,
        )
