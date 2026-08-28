"""测试不确定性置信度映射（Phase 2）。"""

import pytest

from trade_krono_cli.configs.schema import ScoringConfig
from trade_krono_cli.pipeline.merge import _uncertainty_confidence_bonus, default_scorer

SCORING = ScoringConfig()


class TestUncertaintyConfidenceBonus:
    """_uncertainty_confidence_bonus 函数单元测试。"""

    def test_none_pu(self):
        assert _uncertainty_confidence_bonus(None, SCORING) == 0.0

    def test_empty_dict(self):
        assert _uncertainty_confidence_bonus({}, SCORING) == 0.0

    def test_missing_confidence_score(self):
        assert _uncertainty_confidence_bonus({"direction": "UP"}, SCORING) == 0.0

    def test_high_confidence(self):
        """confidence_score >= 70 → +3"""
        assert _uncertainty_confidence_bonus({"confidence_score": 70.0}, SCORING) == 3.0
        assert _uncertainty_confidence_bonus({"confidence_score": 85.0}, SCORING) == 3.0
        assert _uncertainty_confidence_bonus({"confidence_score": 100.0}, SCORING) == 3.0

    def test_medium_confidence(self):
        """50 <= confidence_score < 70 → +1"""
        assert _uncertainty_confidence_bonus({"confidence_score": 50.0}, SCORING) == 1.0
        assert _uncertainty_confidence_bonus({"confidence_score": 60.0}, SCORING) == 1.0
        assert _uncertainty_confidence_bonus({"confidence_score": 69.9}, SCORING) == 1.0

    def test_low_confidence(self):
        """confidence_score < 50 → -2"""
        assert _uncertainty_confidence_bonus({"confidence_score": 49.9}, SCORING) == -2.0
        assert _uncertainty_confidence_bonus({"confidence_score": 30.0}, SCORING) == -2.0
        assert _uncertainty_confidence_bonus({"confidence_score": 0.0}, SCORING) == -2.0


class TestDefaultScorerWithUncertainty:
    """验证不确定置信度映射正确参与打分。"""

    def test_high_conf_boosts_score(self):
        """高置信度股票得分高于低置信度（其他条件相同）。"""
        high_conf = {
            "ta_confidence": 80.0,
            "kronos_change_pct": 3.0,
            "kronos_direction": "UP",
            "kronos_prediction_uncertainty": {"confidence_score": 80.0},
        }
        low_conf = {
            "ta_confidence": 80.0,
            "kronos_change_pct": 3.0,
            "kronos_direction": "UP",
            "kronos_prediction_uncertainty": {"confidence_score": 30.0},
        }
        score_high = default_scorer(high_conf)
        score_low = default_scorer(low_conf)
        # 差值应为 (8+3) - (3-2) = 11 - 1 = 10 分
        assert score_high - score_low == pytest.approx(10.0, abs=0.1)

    def test_no_uncertainty_no_penalty(self):
        """无不确定性数据时不应有 bonus/penalty。"""
        merged = {
            "ta_confidence": 80.0,
            "kronos_change_pct": 3.0,
            "kronos_direction": "UP",
            "kronos_prediction_uncertainty": None,
        }
        score = default_scorer(merged)
        # 无 bonus/penalty，仅基础分
        expected = 0.4 * 80 + 0.3 * 53 + 0.1 * 10  # 32 + 15.9 + 1 = 48.9
        assert score == pytest.approx(expected, abs=0.1)

    def test_zero_uncertainty_score(self):
        """confidence_score=0 → -2 penalty。"""
        merged = {
            "ta_confidence": 80.0,
            "kronos_change_pct": 3.0,
            "kronos_direction": "UP",
            "kronos_prediction_uncertainty": {"confidence_score": 0.0},
        }
        score = default_scorer(merged)
        # 基础 48.9 - 2 = 46.9
        assert score == pytest.approx(46.9, abs=0.1)
