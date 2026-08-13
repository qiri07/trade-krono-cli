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

    公式：
      score = TA_conf × w_ta
            + clamp(chg + offset, 0, 100) × w_chg
            + direction_bonus
            + uncertainty_score × w_unc
            + uncertainty_bonus/penalty
            - risk_score × w_risk × 100

    其中方向加成范围 ±1 分，风险惩罚最多扣 w_risk×100 分（默认 -15）。
    """

    name = "linear"

    def _score_impl(self, merged: dict, config: Optional[ScoringConfig] = None) -> float:
        s = config or ScoringConfig()

        score = 0.0
        components: dict[str, float] = {}

        # TA 置信度（0–40）
        ta_conf = merged.get("ta_confidence") or 0
        ta_score = max(0, min(100, ta_conf)) * s.ta_confidence_weight
        score += ta_score
        components["ta_confidence"] = ta_score

        # 预期涨跌幅（0–30）
        # Risk Engine v2：若存在 adjusted_expected_return，使用风险模型已调整的收益率；
        # 否则使用原始收益率（风险惩罚在下方单独扣除）。
        adj_ret = merged.get("adjusted_expected_return")
        if adj_ret is not None:
            # Risk model has already adjusted the expected return
            chg = adj_ret
        else:
            chg = merged.get("kronos_change_pct") or merged.get(
                "kronos_change_pct_gross"
            ) or 0
        chg_score = max(0, min(100, chg + s.change_pct_offset)) * s.change_pct_weight
        score += chg_score
        components["change_pct"] = chg_score

        # 方向加成（±1 分）
        direction = merged.get("kronos_direction")
        dir_bonus = 0.0
        if direction == "UP":
            dir_bonus = s.direction_base_weight * s.direction_bonus_point
        elif direction == "DOWN":
            dir_bonus = s.direction_base_weight * (-s.direction_bonus_point)
        score += dir_bonus
        components["direction_bonus"] = dir_bonus

        # 预测不确定性（0–10）+ 置信度微调（±3/±1/-2）
        pu = merged.get("kronos_prediction_uncertainty")
        unc_bonus = 0.0
        if pu:
            cs = pu.get("confidence_score") or 0
            unc_score = max(0, min(100, cs)) * s.uncertainty_base_weight
            score += unc_score
            components["uncertainty"] = unc_score

            # 置信度 bonus/penalty
            if cs >= s.uncertainty_high_threshold:
                unc_bonus = s.uncertainty_high_bonus
            elif cs >= s.uncertainty_med_threshold:
                unc_bonus = s.uncertainty_med_bonus
            else:
                unc_bonus = s.uncertainty_low_penalty
            score += unc_bonus
            components["uncertainty_bonus"] = unc_bonus

        # 风险惩罚（仅在没有 adjusted_expected_return 时应用，否则风险已融入收益率）
        if merged.get("adjusted_expected_return") is None:
            total_risk = merged.get("risk_score_total") or 0
            risk_penalty = (total_risk / 100.0) * s.risk_penalty_weight * 100
            score -= risk_penalty
            components["risk_penalty"] = -risk_penalty

        final = round(max(0, min(100, score)), 2)
        return final


# ═══════════════════════════════════════════════════════
# MultiplicativeScorer（风险敏感型）
# ═══════════════════════════════════════════════════════

class MultiplicativeScorer(CompositeScorer):
    """
    乘法衰减型打分器。

    核心思想：风险越高，基础分被压缩得越多，
    适合风险厌恶型策略——即使 TA/Kronos 看好，
    高风险股票也会被打到低分。

    公式：
      base = TA_conf × w_ta + chg_mapped × w_chg + dir_bonus + unc_score + unc_bonus
      risk_factor = 1.0 - (risk_score / 100.0) × risk_penalty_weight
      final = base × risk_factor

    与 Linear 的区别：风险惩罚是乘法而非减法，
    高风险股票的得分会被更强烈地压缩。
    """

    name = "multiplicative"

    def _score_impl(self, merged: dict, config: Optional[ScoringConfig] = None) -> float:
        s = config or ScoringConfig()

        # 先计算基础分（同 Linear，但不含风险惩罚）
        ta_conf = merged.get("ta_confidence") or 0
        base = max(0, min(100, ta_conf)) * s.ta_confidence_weight

        chg = merged.get("kronos_change_pct") or merged.get("kronos_change_pct_gross") or 0
        # Risk Engine v2：使用调整后的预期收益（若可用）
        adj_ret = merged.get("adjusted_expected_return")
        if adj_ret is not None:
            chg = adj_ret
        base += max(0, min(100, chg + s.change_pct_offset)) * s.change_pct_weight

        direction = merged.get("kronos_direction")
        if direction == "UP":
            base += s.direction_base_weight * s.direction_bonus_point
        elif direction == "DOWN":
            base += s.direction_base_weight * (-s.direction_bonus_point)

        pu = merged.get("kronos_prediction_uncertainty")
        if pu:
            cs = pu.get("confidence_score") or 0
            base += max(0, min(100, cs)) * s.uncertainty_base_weight
            if cs >= s.uncertainty_high_threshold:
                base += s.uncertainty_high_bonus
            elif cs >= s.uncertainty_med_threshold:
                base += s.uncertainty_med_bonus
            else:
                base += s.uncertainty_low_penalty

        # 风险乘法因子（仅在没有 adjusted_expected_return 时应用，避免双重惩罚）
        if merged.get("adjusted_expected_return") is None:
            total_risk = merged.get("risk_score_total") or 0
            risk_factor = 1.0 - (total_risk / 100.0) * s.risk_penalty_weight
        else:
            risk_factor = 1.0  # 风险已融入调整后的收益率

        final = base * risk_factor
        return round(max(0, min(100, final)), 2)


# ═══════════════════════════════════════════════════════
# RankBasedScorer（百分位排名）
# ═══════════════════════════════════════════════════════

class RankBasedScorer(CompositeScorer):
    """
    百分位排名转换打分器。

    不直接计算绝对分数，而是根据股票在股票池中的排名
    转换为百分位得分。排名越靠前（综合指标越好），得分越高。

    公式：
      rank_score = (rank / n) × 100
      其中 rank=1 表示综合指标最好（TA+Kronos+低风险），n 为总股票数。

    适用场景：
      - 关注相对排序而非绝对阈值
      - 不同市场环境下分数分布差异大时，排名更稳定
    """

    name = "rank_based"

    def _score_impl(self, merged: dict, config: Optional[ScoringConfig] = None) -> float:
        rank = merged.get("rank")
        if rank is None or rank <= 0:
            # 无排名信息时退化为 Linear
            return LinearScorer()._score_impl(merged, config)

        n = merged.get("_pool_size", 1)
        if n <= 0:
            n = 1

        # rank=1（最好）→ score≈100，rank=n（最差）→ score≈0
        score = (1.0 - (rank - 1) / max(1, n - 1)) * 100.0
        return round(max(0, min(100, score)), 2)
