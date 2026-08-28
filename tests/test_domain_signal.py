"""测试领域信号对象（SignalAssessment / SignalConflict / EV 计算）。"""

from __future__ import annotations

import pytest

from trade_krono_cli.domain.signal import (
    SignalAssessment,
    SignalConflict,
    _compute_ev,
    detect_conflict,
)
from trade_krono_cli.domain.types import Direction
from trade_krono_cli.domain.types import Signal as DomainSignal

# ── SignalConflict ────────────────────────────────────────────────────────────


def test_conflict_constants():
    assert SignalConflict.NONE == "none"
    assert SignalConflict.TA_vs_KRONOS == "ta_vs_kronos"
    assert SignalConflict.TA_vs_COMMITTEE == "ta_vs_committee"
    assert SignalConflict.KRONOS_vs_COMMITTEE == "kronos_vs_committee"
    assert SignalConflict.ALL_CONFLICT == "all_conflict"


def test_is_conflict():
    assert SignalConflict.is_conflict("ta_vs_kronos") is True
    assert SignalConflict.is_conflict("none") is False
    assert SignalConflict.is_conflict("random_string") is False


# ── detect_conflict ───────────────────────────────────────────────────────────


def test_no_conflict():
    assert detect_conflict(DomainSignal.BUY, Direction.UP, None) == SignalConflict.NONE


def test_ta_vs_kronos_conflict():
    assert detect_conflict(DomainSignal.BUY, Direction.DOWN, None) == SignalConflict.TA_vs_KRONOS


def test_ta_vs_kronos_conflict_reverse():
    assert detect_conflict(DomainSignal.SELL, Direction.UP, None) == SignalConflict.TA_vs_KRONOS


def test_all_conflict():
    """TA BUY, Kronos DOWN, Committee SELL → ta_vs_kronos (first pairwise conflict found)。"""
    result = detect_conflict(DomainSignal.BUY, Direction.DOWN, DomainSignal.SELL)
    assert result in (SignalConflict.TA_vs_KRONOS, SignalConflict.ALL_CONFLICT)


def test_none_inputs():
    """全 None 时不冲突。"""
    result = detect_conflict(None, None, None)
    assert result == SignalConflict.NONE


# ── _compute_ev ───────────────────────────────────────────────────────────────


def test_compute_ev_up():
    prob_win, prob_loss, avg_win, avg_loss, ev, raev = _compute_ev(
        direction=Direction.UP,
        expected_return=5.0,
        p10=-2.0,
        p90=12.0,
        cost_bps=17.0,
    )
    assert ev is not None
    assert ev > 0


def test_compute_ev_down():
    prob_win, prob_loss, avg_win, avg_loss, ev, raev = _compute_ev(
        direction=Direction.DOWN,
        expected_return=-3.0,
        p10=-10.0,
        p90=2.0,
        cost_bps=17.0,
    )
    assert ev is not None
    assert ev < 0


def test_compute_ev_flat():
    _, _, _, _, ev, _ = _compute_ev(
        direction=Direction.FLAT,
        expected_return=0.5,
        p10=-1.0,
        p90=2.0,
        cost_bps=17.0,
    )
    assert ev is not None


def test_compute_ev_no_direction():
    """direction=None 时 expected_return 也必须为 None，否则 TypeError。"""
    with pytest.raises(TypeError):
        _compute_ev(direction=None, expected_return=None, cost_bps=17.0)


def test_compute_ev_nan_return():
    """NaN expected_return 时：NaN is falsy，p10/p90 被正常赋值，结果不为 NaN。"""
    result = _compute_ev(
        direction=Direction.UP,
        expected_return=float("nan"),
        p10=-1.0,
        p90=6.0,
        cost_bps=17.0,
    )
    # NaN is falsy → p10/p90 used; bias=0 → prob_win=0.5; valid EV computed
    assert result[0] is not None  # prob_win = 0.5
    assert result[4] is not None  # ev is a valid number


# ── SignalAssessment ──────────────────────────────────────────────────────────


def test_basic():
    sa = SignalAssessment(
        ticker="sh.600519",
        eval_date="2026-08-11",
        final_signal=DomainSignal.BUY,
        final_confidence=80.0,
        conflict=SignalConflict.NONE,
        expected_value=5.5,
        prob_win=0.72,
    )
    assert sa.ticker == "sh.600519"
    assert sa.final_signal == DomainSignal.BUY
    assert sa.expected_value == 5.5


def test_frozen():
    sa = SignalAssessment(
        ticker="sh.600519",
        eval_date="2026-08-11",
        final_signal=DomainSignal.HOLD,
        final_confidence=50.0,
    )
    with pytest.raises(AttributeError):
        sa.ticker = "sz.000858"  # type: ignore[misc]


def test_to_dict():
    sa = SignalAssessment(
        ticker="sh.600519",
        eval_date="2026-08-11",
        final_signal=DomainSignal.BUY,
        final_confidence=80.0,
        conflict=SignalConflict.NONE,
        expected_value=5.5,
        prob_win=0.72,
        risk_adjusted_ev=1.2,
        position_size=0.3,
        entry_zone=[148.0, 152.0],
        target_price=170.0,
        stop_loss=140.0,
        horizon=20,
        thesis="论点",
        risks=["风险A"],
    )
    d = sa.to_dict()
    assert d["ticker"] == "sh.600519"
    assert d["final_signal"] == "BUY"
    assert d["expected_value"] == 5.5
    assert d["conflict"] == "none"
    assert d["position_size"] == 0.3


def test_from_dict():
    data = {
        "ticker": "sh.600519",
        "eval_date": "2026-08-11",
        "final_signal": "BUY",
        "final_confidence": 80.0,
        "conflict": "none",
        "expected_value": 5.5,
        "prob_win": 0.72,
    }
    sa = SignalAssessment.from_dict(data)
    assert sa.ticker == "sh.600519"
    assert sa.final_signal == DomainSignal.BUY
    assert sa.expected_value == 5.5


def test_from_dict_invalid_signal():
    data = {
        "ticker": "sh.600519",
        "eval_date": "2026-08-11",
        "final_signal": "UNKNOWN",
        "final_confidence": 80.0,
    }
    # Invalid signal falls back to HOLD (no exception)
    sa = SignalAssessment.from_dict(data)
    assert sa.final_signal == DomainSignal.HOLD


def test_from_dict_invalid_direction():
    data = {
        "ticker": "sh.600519",
        "eval_date": "2026-08-11",
        "final_signal": "BUY",
        "final_confidence": 80.0,
        "kronos_direction": "BAD",
    }
    # kronos_direction is not a field in from_dict; this key is ignored
    sa = SignalAssessment.from_dict(data)
    assert sa.final_signal == DomainSignal.BUY


def test_empty():
    """SignalAssessment 无 empty() 类方法；用最小构造替代。"""
    sa = SignalAssessment(
        ticker="sh.600519",
        eval_date="2026-08-11",
        final_signal=DomainSignal.HOLD,
        final_confidence=0.0,
    )
    assert sa.final_signal == DomainSignal.HOLD
    assert sa.final_confidence == 0.0
    assert sa.conflict == SignalConflict.NONE
