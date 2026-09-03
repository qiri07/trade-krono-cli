"""测试领域工厂函数（domain/factory.py）。"""

from __future__ import annotations

import pytest

from trade_krono_cli.domain.factory import (
    _majority_vote,
    build_eval_record,
    build_investment_decision,
    build_signal_assessment,
)
from trade_krono_cli.domain.prediction import KronosPrediction, PredictionDistribution, TAAnalysis
from trade_krono_cli.domain.signal import SignalAssessment, SignalConflict
from trade_krono_cli.domain.types import Direction
from trade_krono_cli.domain.types import Signal as DomainSignal

# ── _majority_vote ────────────────────────────────────────────────────────────


def test_majority_vote_single() -> None:
    votes = [(DomainSignal.BUY, 80.0, "ta")]
    sig, conf = _majority_vote(votes)
    assert sig == DomainSignal.BUY
    assert conf > 0


def test_majority_vote_two_same() -> None:
    votes = [
        (DomainSignal.BUY, 80.0, "ta"),
        (DomainSignal.BUY, 70.0, "kronos"),
    ]
    sig, _conf = _majority_vote(votes)
    assert sig == DomainSignal.BUY


def test_majority_vote_two_different() -> None:
    """不同信号时取置信度高的。"""
    votes = [
        (DomainSignal.BUY, 80.0, "ta"),
        (DomainSignal.SELL, 60.0, "kronos"),
    ]
    sig, _conf = _majority_vote(votes)
    assert sig == DomainSignal.BUY


def test_majority_vote_empty() -> None:
    sig, conf = _majority_vote([])
    assert sig == DomainSignal.HOLD
    assert conf == 50.0


# ── build_signal_assessment ───────────────────────────────────────────────────


def test_build_only_ta() -> None:
    ta = TAAnalysis(
        ticker="sh.600519",
        eval_date="2026-08-11",
        signal=DomainSignal.BUY,
        confidence=80.0,
        thesis="基本面良好",
    )
    sa = build_signal_assessment(
        ticker="sh.600519",
        eval_date="2026-08-11",
        ta=ta,
    )
    assert sa.final_signal == DomainSignal.BUY
    assert sa.final_confidence > 0
    assert sa.conflict == SignalConflict.NONE


def test_build_only_kronos() -> None:
    kd = PredictionDistribution(
        expected_return=3.2,
        direction=Direction.UP,
        direction_score=0.8,
        confidence_score=75.0,
    )
    kronos = KronosPrediction(
        ticker="sh.600519",
        eval_date="2026-08-11",
        horizon=5,
        predicted_close=155.0,
        direction=Direction.UP,
        expected_return=3.2,
        distribution=kd,
    )
    sa = build_signal_assessment(
        ticker="sh.600519",
        eval_date="2026-08-11",
        kronos=kronos,
    )
    assert sa.final_signal == DomainSignal.BUY
    assert sa.expected_value is not None


def test_build_ta_and_kronos_conflict() -> None:
    ta = TAAnalysis(
        ticker="sh.600519",
        eval_date="2026-08-11",
        signal=DomainSignal.BUY,
        confidence=80.0,
    )
    kronos = KronosPrediction(
        ticker="sh.600519",
        eval_date="2026-08-11",
        horizon=5,
        predicted_close=145.0,
        direction=Direction.DOWN,
        expected_return=-2.0,
        distribution=PredictionDistribution(
            expected_return=-2.0,
            direction=Direction.DOWN,
            direction_score=0.6,
            confidence_score=60.0,
        ),
    )
    sa = build_signal_assessment(
        ticker="sh.600519",
        eval_date="2026-08-11",
        ta=ta,
        kronos=kronos,
    )
    # TA BUY + Kronos DOWN → 冲突
    assert sa.conflict in (SignalConflict.TA_vs_KRONOS, SignalConflict.ALL_CONFLICT)


def test_build_with_committee() -> None:
    sa = build_signal_assessment(
        ticker="sh.600519",
        eval_date="2026-08-11",
        committee_rec=DomainSignal.BUY,
        committee_confidence=85.0,
    )
    assert sa.final_signal == DomainSignal.BUY


def test_build_all_none() -> None:
    """所有输入均为 None 时，final_signal = HOLD, conflict = NONE。"""
    sa = build_signal_assessment(
        ticker="sh.600519",
        eval_date="2026-08-11",
    )
    assert sa.final_signal == DomainSignal.HOLD
    assert sa.conflict == SignalConflict.NONE


def test_build_with_bull_bear_case() -> None:
    sa = build_signal_assessment(
        ticker="sh.600519",
        eval_date="2026-08-11",
        bull_case="业绩超预期",
        bear_case="估值偏高",
    )
    assert "Bull" in sa.thesis


def test_build_custom_cost_bps() -> None:
    sa = build_signal_assessment(
        ticker="sh.600519",
        eval_date="2026-08-11",
        ta=TAAnalysis(
            ticker="sh.600519",
            eval_date="2026-08-11",
            signal=DomainSignal.BUY,
            confidence=80.0,
        ),
        cost_bps=30.0,
    )
    assert sa.cost_bps == 30.0


# ── build_investment_decision ─────────────────────────────────────────────────


def test_build_decision_from_assessment() -> None:
    assessment = SignalAssessment(
        ticker="sh.600519",
        eval_date="2026-08-11",
        final_signal=DomainSignal.BUY,
        final_confidence=80.0,
        expected_value=5.5,
        prob_win=0.72,
        thesis="论点",
        risks=["风险"],
    )
    d = build_investment_decision(
        ticker="sh.600519",
        eval_date="2026-08-11",
        signal_assessment=assessment,
    )
    assert d.ticker == "sh.600519"
    assert d.signal == DomainSignal.BUY
    assert d.expected_value == 5.5
    assert d.prob_win == 0.72
    assert d.thesis == "论点"


def test_build_decision_with_risk() -> None:
    from trade_krono_cli.domain.risk import RiskAssessment

    assessment = SignalAssessment(
        ticker="sh.600519",
        eval_date="2026-08-11",
        final_signal=DomainSignal.BUY,
        final_confidence=80.0,
    )
    risk = RiskAssessment(ticker="sh.600519", eval_date="2026-08-11", risk_score_total=35.0)
    d = build_investment_decision(
        ticker="sh.600519",
        eval_date="2026-08-11",
        signal_assessment=assessment,
        risk_assessment=risk,
    )
    assert d.risk_assessment == risk


def test_build_decision_with_params() -> None:
    assessment = SignalAssessment(
        ticker="sh.600519",
        eval_date="2026-08-11",
        final_signal=DomainSignal.BUY,
        final_confidence=80.0,
    )
    d = build_investment_decision(
        ticker="sh.600519",
        eval_date="2026-08-11",
        signal_assessment=assessment,
        position_size=0.3,
        entry_zone=[148.0, 152.0],
        target_price=170.0,
        stop_loss=140.0,
        horizon=20,
    )
    assert d.position_size == 0.3
    assert d.entry_zone == [148.0, 152.0]
    assert d.target_price == 170.0
    assert d.stop_loss == 140.0
    assert d.horizon == 20


# ── build_eval_record ─────────────────────────────────────────────────────────


def test_build_eval_record() -> None:
    record = build_eval_record(
        ticker="sh.600519",
        eval_date="2026-08-11",
        horizon_days=5,
        pred_direction=Direction.UP,
        pred_return_pct=3.2,
        actual_return_pct=2.5,
        ta_signal=DomainSignal.BUY,
        ranking_score=75.0,
        expected_value=5.0,
    )
    assert record.ticker == "sh.600519"
    assert record.pred_direction == Direction.UP
    assert record.actual_direction == "UP"  # 2.5% > 1.0 threshold
    assert record.is_direction_correct is True
    assert record.error_pct == pytest.approx(3.2 - 2.5, abs=0.01)


def test_build_eval_record_flat() -> None:
    record = build_eval_record(
        ticker="sh.600519",
        eval_date="2026-08-11",
        horizon_days=5,
        pred_direction=Direction.UP,
        pred_return_pct=3.2,
        actual_return_pct=0.5,  # < 1.0 → FLAT
    )
    assert record.actual_direction == "FLAT"
    assert record.is_direction_correct is False


def test_build_eval_record_down() -> None:
    record = build_eval_record(
        ticker="sh.600519",
        eval_date="2026-08-11",
        horizon_days=5,
        pred_direction=Direction.DOWN,
        pred_return_pct=-3.0,
        actual_return_pct=-5.0,
    )
    assert record.actual_direction == "DOWN"
    assert record.is_direction_correct is True


def test_build_eval_record_with_distribution() -> None:
    record = build_eval_record(
        ticker="sh.600519",
        eval_date="2026-08-11",
        horizon_days=5,
        pred_direction=Direction.UP,
        pred_return_pct=3.2,
        actual_return_pct=2.5,
        p10=-1.0,
        p25=0.5,
        p50=3.0,
        p75=5.0,
        p90=8.0,
    )
    assert record.p10 == -1.0
    assert record.p90 == 8.0


def test_build_eval_record_composite_score_backward_compat() -> None:
    """composite_score 参数作为 ranking_score 的别名。"""
    record = build_eval_record(
        ticker="sh.600519",
        eval_date="2026-08-11",
        horizon_days=5,
        pred_direction=Direction.UP,
        pred_return_pct=2.0,
        actual_return_pct=1.5,
        composite_score=70.0,
    )
    assert record.ranking_score == 70.0
    assert record.composite_score == 70.0
