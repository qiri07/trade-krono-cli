"""
scoring.scorers — 三种综合打分策略实现。

策略列表：
  linear       : 加权线性组合（默认，与原 default_scorer 等价）
  multiplicative: 乘法衰减型（高风险→分数指数衰减）
  rank_based   : 百分位排名转换（相对排序优先于绝对分）

Risk Engine v2 集成：
  - 若 merged 包含 adjusted_expected_return（风险模型已调整预期收益），
    直接使用调整后的收益率参与打分，替代旧的线性风险惩罚。
  - 若未调整（legacy），退化为原有的 risk_penalty 扣分逻辑。

语义说明（V0.3 升级）：
  返回值是 ranking_score（0-100），作为辅助排序分使用。
  真正的金融决策指标在 SignalAssessment.expected_value / prob_win / risk_adjusted_ev。
  Pipeline 应以 EV 为主要排序依据，ranking_score 仅作辅助。
"""
from __future__ import annotations

from typing import Optional

from trade_krono_cli.configs.scoring import ScoringConfig
from trade_krono_cli.scoring.base import CompositeScorer


# ═══════════════════════════════════════════════════════
# LinearScorer（默认）
# ═══════════════════════════════════════════════════════

class LinearScorer(CompositeScorer):
    """
    加权线性综合打分器（与原 default_scorer 等价）。

    输出 ranking_score（0-100），作为辅助排序分使用。
    真正的决策依据应是 SignalAssessment.expected_value。

    公式：
      raw_score = TA_conf × w_ta
                + clamp(chg + offset, 0, 100) × w_chg
                + direction_bonus
                + uncertainty_score × w_unc
                + uncertainty_bonus/penalty
                - risk_score × w_risk × 100

      ranking_score = clamp(raw_score, 0, 100)
    """

    name = "linear"

    def _score_impl(self, merged: dict, config: Optional[ScoringConfig] = None) -> float:
        s = config or ScoringConfig()

        raw_score = 0.0
        components: dict[str, float] = {}

        ta_conf = merged.get("ta_confidence") or 0
        ta_score = max(0, min(100, ta_conf)) * s.ta_confidence_weight
        raw_score += ta_score
        components["ta_confidence"] = ta_score

        adj_ret = merged.get("adjusted_expected_return")
        if adj_ret is not None:
            chg = adj_ret
        else:
            chg = merged.get("kronos_change_pct") or merged.get(
                "kronos_change_pct_gross"
            ) or 0
        chg_score = max(0, min(100, chg + s.change_pct_offset)) * s.change_pct_weight
        raw_score += chg_score
        components["change_pct"] = chg_score

        direction = merged.get("kronos_direction")
        dir_bonus = 0.0
        if direction == "UP":
            dir_bonus = s.direction_base_weight * s.direction_bonus_point
        elif direction == "DOWN":
            dir_bonus = s.direction_base_weight * (-s.direction_bonus_point)
        raw_score += dir_bonus
        components["direction_bonus"] = dir_bonus

        pu = merged.get("kronos_prediction_uncertainty")
        unc_bonus = 0.0
        if pu:
            cs = pu.get("confidence_score") or 0
            unc_score = max(0, min(100, cs)) * s.uncertainty_base_weight
            raw_score += unc_score
            components["uncertainty"] = unc_score

            if cs >= s.uncertainty_high_threshold:
                unc_bonus = s.uncertainty_high_bonus
            elif cs >= s.uncertainty_med_threshold:
                unc_bonus = s.uncertainty_med_bonus
            else:
                unc_bonus = s.uncertainty_low_penalty
            raw_score += unc_bonus
            components["uncertainty_bonus"] = unc_bonus

        if merged.get("adjusted_expected_return") is None:
            total_risk = merged.get("risk_score_total") or 0
            risk_penalty = (total_risk / 100.0) * s.risk_penalty_weight * 100
            raw_score -= risk_penalty
            components["risk_penalty"] = -risk_penalty

        final = round(max(0, min(100, raw_score)), 2)
        return final


# ═══════════════════════════════════════════════════════
# MultiplicativeScorer（风险敏感型）
# ═══════════════════════════════════════════════════════

class MultiplicativeScorer(CompositeScorer):
    """
    乘法衰减型打分器。

    输出 ranking_score（0-100），作为辅助排序分使用。
    真正的决策依据应是 SignalAssessment.expected_value。

    公式：
      raw_score = TA_conf × w_ta + chg_mapped × w_chg + dir_bonus + unc_score + unc_bonus
      risk_factor = 1.0 - (risk_score / 100.0) × risk_penalty_weight
      ranking_score = clamp(raw_score × risk_factor, 0, 100)
    """

    name = "multiplicative"

    def _score_impl(self, merged: dict, config: Optional[ScoringConfig] = None) -> float:
        s = config or ScoringConfig()

        ta_conf = merged.get("ta_confidence") or 0
        raw_score = max(0, min(100, ta_conf)) * s.ta_confidence_weight

        chg = merged.get("kronos_change_pct") or merged.get("kronos_change_pct_gross") or 0
        adj_ret = merged.get("adjusted_expected_return")
        if adj_ret is not None:
            chg = adj_ret
        raw_score += max(0, min(100, chg + s.change_pct_offset)) * s.change_pct_weight

        direction = merged.get("kronos_direction")
        if direction == "UP":
            raw_score += s.direction_base_weight * s.direction_bonus_point
        elif direction == "DOWN":
            raw_score += s.direction_base_weight * (-s.direction_bonus_point)

        pu = merged.get("kronos_prediction_uncertainty")
        if pu:
            cs = pu.get("confidence_score") or 0
            raw_score += max(0, min(100, cs)) * s.uncertainty_base_weight
            if cs >= s.uncertainty_high_threshold:
                raw_score += s.uncertainty_high_bonus
            elif cs >= s.uncertainty_med_threshold:
                raw_score += s.uncertainty_med_bonus
            else:
                raw_score += s.uncertainty_low_penalty

        if merged.get("adjusted_expected_return") is None:
            total_risk = merged.get("risk_score_total") or 0
            risk_factor = 1.0 - (total_risk / 100.0) * s.risk_penalty_weight
        else:
            risk_factor = 1.0

        final = raw_score * risk_factor
        return round(max(0, min(100, final)), 2)


# ═══════════════════════════════════════════════════════
# RankBasedScorer（百分位排名）
# ═══════════════════════════════════════════════════════

class RankBasedScorer(CompositeScorer):
    """
    百分位排名转换打分器。

    输出 ranking_score（0-100），作为辅助排序分使用。
    真正的决策依据应是 SignalAssessment.expected_value。

    公式：
      rank_raw = (1.0 - (rank - 1) / max(1, n - 1)) × 100
      ranking_score = clamp(rank_raw, 0, 100)
    """

    name = "rank_based"

    def _score_impl(self, merged: dict, config: Optional[ScoringConfig] = None) -> float:
        rank = merged.get("rank")
        if rank is None or rank <= 0:
            return LinearScorer()._score_impl(merged, config)

        n = merged.get("_pool_size", 1)
        if n <= 0:
            n = 1

        raw_score = (1.0 - (rank - 1) / max(1, n - 1)) * 100.0
        return round(max(0, min(100, raw_score)), 2)
