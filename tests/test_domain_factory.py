"""测试 domain.factory — 领域对象工厂函数。"""

from __future__ import annotations

from trade_krono_cli.domain.factory import build_investment_decision, build_signal_assessment
from trade_krono_cli.domain.prediction import (
    Direction,
    KronosPrediction,
    PredictionDistribution,
    TAAnalysis,
)
from trade_krono_cli.domain.types import Signal


class TestBuildSignalAssessment:
    """测试 build_signal_assessment 工厂函数。"""

    def test_ta_only(self) -> None:
        """仅 TA 信号时，使用 TA 的信号和置信度。"""
        ta = TAAnalysis(
            ticker="sh.600519",
            eval_date="2026-09-01",
            signal="BUY",
            confidence=85.0,
            thesis="Strong fundamentals",
            reasoning="",
            risks=["market"],
            invalidations=[],
            error=None,
            elapsed_sec=1.0,
        )

        result = build_signal_assessment(
            ticker="sh.600519",
            eval_date="2026-09-01",
            ta=ta,
        )

        assert result.final_signal == Signal.BUY
        assert result.conflict == "none"
        # final_confidence 经过加权计算，应大于 0
        assert result.final_confidence > 0
        assert result.final_confidence <= 100.0

    def test_kronos_only(self) -> None:
        """仅 Kronos 信号时，使用 Kronos 的方向映射。"""
        dist = PredictionDistribution(
            expected_return=3.2,
            direction=Direction.UP,
            direction_score=0.8,
            confidence_score=75.0,
        )
        kronos = KronosPrediction(
            ticker="sh.600519",
            eval_date="2026-09-01",
            horizon=30,
            direction=Direction.UP,
            expected_return=3.2,
            predicted_close=1837.0,
            distribution=dist,
        )

        result = build_signal_assessment(
            ticker="sh.600519",
            eval_date="2026-09-01",
            kronos=kronos,
        )

        assert result.final_signal == Signal.BUY
        assert result.conflict == "none"

    def test_ta_kronos_conflict(self) -> None:
        """TA BUY + Kronos DOWN 应检测到冲突。"""
        ta = TAAnalysis(
            ticker="sh.600519",
            eval_date="2026-09-01",
            signal="BUY",
            confidence=80.0,
            thesis="",
            reasoning="",
            risks=[],
            invalidations=[],
            error=None,
            elapsed_sec=1.0,
        )
        dist = PredictionDistribution(
            expected_return=-2.0,
            direction=Direction.DOWN,
            direction_score=0.6,
            confidence_score=60.0,
        )
        kronos = KronosPrediction(
            ticker="sh.600519",
            eval_date="2026-09-01",
            horizon=30,
            direction=Direction.DOWN,
            expected_return=-2.0,
            predicted_close=1744.0,
            distribution=dist,
        )

        result = build_signal_assessment(
            ticker="sh.600519",
            eval_date="2026-09-01",
            ta=ta,
            kronos=kronos,
        )

        assert result.conflict == "ta_vs_kronos"

    def test_committee_override(self) -> None:
        """委员会推荐应覆盖多数表决结果。"""
        ta = TAAnalysis(
            ticker="sh.600519",
            eval_date="2026-09-01",
            signal="BUY",
            confidence=80.0,
            thesis="",
            reasoning="",
            risks=[],
            invalidations=[],
            error=None,
            elapsed_sec=1.0,
        )
        dist = PredictionDistribution(
            expected_return=0.5,
            direction=Direction.FLAT,
            direction_score=0.5,
            confidence_score=55.0,
        )
        kronos = KronosPrediction(
            ticker="sh.600519",
            eval_date="2026-09-01",
            horizon=30,
            direction=Direction.FLAT,
            expected_return=0.5,
            predicted_close=1789.0,
            distribution=dist,
        )

        result = build_signal_assessment(
            ticker="sh.600519",
            eval_date="2026-09-01",
            ta=ta,
            kronos=kronos,
            committee_rec=Signal.HOLD,
            committee_confidence=70.0,
        )

        assert result.final_signal == Signal.HOLD
        # committee_rec 会覆盖最终信号，但置信度可能因多数表决重新计算
        assert result.committee_rec == Signal.HOLD

    def test_all_none_returns_hold(self) -> None:
        """所有输入都为 None 时，默认返回 HOLD。"""
        result = build_signal_assessment(
            ticker="sh.600519",
            eval_date="2026-09-01",
        )

        assert result.final_signal == Signal.HOLD
        assert result.final_confidence == 50.0

    def test_ev_calculation(self) -> None:
        """EV 计算应正确传播。"""
        ta = TAAnalysis(
            ticker="sh.600519",
            eval_date="2026-09-01",
            signal="BUY",
            confidence=80.0,
            thesis="",
            reasoning="",
            risks=[],
            invalidations=[],
            error=None,
            elapsed_sec=1.0,
        )
        dist = PredictionDistribution(
            expected_return=3.0,
            direction=Direction.UP,
            direction_score=0.8,
            confidence_score=75.0,
            p10=-1.0,
            p90=5.0,
        )
        kronos = KronosPrediction(
            ticker="sh.600519",
            eval_date="2026-09-01",
            horizon=30,
            direction=Direction.UP,
            expected_return=3.0,
            predicted_close=1834.0,
            distribution=dist,
        )

        result = build_signal_assessment(
            ticker="sh.600519",
            eval_date="2026-09-01",
            ta=ta,
            kronos=kronos,
            cost_bps=17.0,
        )

        assert result.expected_value is not None
        assert result.prob_win is not None
        assert result.prob_loss is not None


class TestBuildInvestmentDecision:
    """测试 build_investment_decision 工厂函数。"""

    def test_basic_decision(self) -> None:
        """基本决策构建。"""
        assessment = build_signal_assessment(
            ticker="sh.600519",
            eval_date="2026-09-01",
        )

        decision = build_investment_decision(
            ticker="sh.600519",
            eval_date="2026-09-01",
            signal_assessment=assessment,
            ranking_score=75.0,
        )

        assert decision.ticker == "sh.600519"
        assert decision.signal == Signal.HOLD
        assert decision.ranking_score == 75.0
        assert decision.expected_value == assessment.expected_value

    def test_backward_compatible_composite_score(self) -> None:
        """向后兼容：composite_score 应映射到 ranking_score。"""
        assessment = build_signal_assessment(
            ticker="sh.600519",
            eval_date="2026-09-01",
        )

        decision = build_investment_decision(
            ticker="sh.600519",
            eval_date="2026-09-01",
            signal_assessment=assessment,
            composite_score=80.0,  # 旧参数名
        )

        assert decision.ranking_score == 80.0

    def test_decision_with_position_sizing(self) -> None:
        """带仓位管理的决策构建。"""
        assessment = build_signal_assessment(
            ticker="sh.600519",
            eval_date="2026-09-01",
        )

        decision = build_investment_decision(
            ticker="sh.600519",
            eval_date="2026-09-01",
            signal_assessment=assessment,
            position_size=0.1,
            entry_zone=[1780.0, 1800.0],
            target_price=1900.0,
            stop_loss=1750.0,
            horizon=30,
        )

        assert decision.position_size == 0.1
        assert decision.entry_zone == [1780.0, 1800.0]
        assert decision.target_price == 1900.0
        assert decision.stop_loss == 1750.0
        assert decision.horizon == 30
