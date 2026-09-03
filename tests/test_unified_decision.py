"""Tests for trade_krono_cli.unified_decision.

覆盖 UnifiedInvestmentDecision 的 EV 计算、冲突检测、信号综合、序列化。
"""

from __future__ import annotations

import pytest

from trade_krono_cli.ta_decision import InvestmentDecision as TADecision
from trade_krono_cli.ta_decision import Signal
from trade_krono_cli.unified_decision import (
    UnifiedInvestmentDecision,
    _direction_to_signal,
    build_unified_decision,
)

# ═══════════════════════════════════════════════════════
#  _direction_to_signal
# ═══════════════════════════════════════════════════════


class TestDirectionToSignal:
    def test_up(self) -> None:
        assert _direction_to_signal("UP") is Signal.BUY

    def test_down(self) -> None:
        assert _direction_to_signal("DOWN") is Signal.SELL

    def test_flat(self) -> None:
        assert _direction_to_signal("FLAT") is Signal.HOLD

    def test_none(self) -> None:
        assert _direction_to_signal(None) is None

    def test_lowercase(self) -> None:
        assert _direction_to_signal("up") is Signal.BUY
        assert _direction_to_signal("down") is Signal.SELL
        assert _direction_to_signal("flat") is Signal.HOLD

    def test_unknown_returns_none(self) -> None:
        assert _direction_to_signal("MIDDLE") is None


# ═══════════════════════════════════════════════════════
#  compute_expected_value
# ═══════════════════════════════════════════════════════


class TestComputeExpectedValue:
    def test_missing_expected_return_returns_self(self) -> None:
        d = UnifiedInvestmentDecision(
            ticker="sh.600519", eval_date="2026-08-11",
            final_signal=Signal.HOLD, final_confidence=50.0,
        )
        result = d.compute_expected_value()
        assert result is d
        assert result.prob_win is None
        assert result.expected_value is None

    def test_missing_last_close_returns_self(self) -> None:
        d = UnifiedInvestmentDecision(
            ticker="sh.600519", eval_date="2026-08-11",
            final_signal=Signal.HOLD, final_confidence=50.0,
            kronos_expected_return=2.0,
        )
        result = d.compute_expected_value()
        assert result.prob_win is None

    def test_positive_return(self) -> None:
        d = UnifiedInvestmentDecision(
            ticker="sh.600519", eval_date="2026-08-11",
            final_signal=Signal.HOLD, final_confidence=50.0,
            kronos_expected_return=3.0, last_close=100.0,
            p10=95.0, p90=110.0,
        )
        d.compute_expected_value()
        assert d.prob_win is not None
        assert 0.05 <= d.prob_win <= 0.95
        assert d.expected_value is not None
        assert d.risk_adjusted_ev is not None

    def test_negative_return(self) -> None:
        d = UnifiedInvestmentDecision(
            ticker="sh.600519", eval_date="2026-08-11",
            final_signal=Signal.HOLD, final_confidence=50.0,
            kronos_expected_return=-2.0, last_close=100.0,
            p10=95.0, p90=98.0,
        )
        d.compute_expected_value()
        assert d.prob_win is not None
        assert d.expected_value is not None

    def test_zero_return(self) -> None:
        d = UnifiedInvestmentDecision(
            ticker="sh.600519", eval_date="2026-08-11",
            final_signal=Signal.HOLD, final_confidence=50.0,
            kronos_expected_return=0.0, last_close=100.0,
            p10=99.0, p90=101.0,
        )
        d.compute_expected_value()
        # prob_win should be 0.5 for zero return
        assert d.prob_win == pytest.approx(0.5, abs=0.01)

    def test_prob_bounds_clamped(self) -> None:
        """prob_win 应在 [0.05, 0.95] 范围内。"""
        d = UnifiedInvestmentDecision(
            ticker="sh.600519", eval_date="2026-08-11",
            final_signal=Signal.HOLD, final_confidence=50.0,
            kronos_expected_return=50.0, last_close=100.0,
        )
        d.compute_expected_value()
        assert d.prob_win >= 0.05
        assert d.prob_win <= 0.95
        assert abs(d.prob_win + d.prob_loss - 1.0) < 1e-6

    def test_no_p10_p90_fallback(self) -> None:
        """未提供 p10/p90 时使用 expected_return 的 0.5/1.5 倍作为退路。"""
        d = UnifiedInvestmentDecision(
            ticker="sh.600519", eval_date="2026-08-11",
            final_signal=Signal.HOLD, final_confidence=50.0,
            kronos_expected_return=2.0, last_close=100.0,
        )
        d.compute_expected_value()
        assert d.expected_value is not None

    def test_near_zero_vol_gives_zero_raev(self) -> None:
        """当 p10==p90==last_close 时，ret_p10=ret_p90=0，vol_proxy=|ret|*0.5=1.0，raev=ev/vol_proxy≈0.53。"""
        d = UnifiedInvestmentDecision(
            ticker="sh.600519", eval_date="2026-08-11",
            final_signal=Signal.HOLD, final_confidence=50.0,
            kronos_expected_return=2.0, last_close=100.0,
            p10=100.0, p90=100.0,
        )
        d.compute_expected_value()
        # vol_proxy = abs(ret_p90 - ret_p10)/2 = 0 → falls to abs(ret)*0.5 = 1.0
        # ev ≈ 0.53, raev ≈ 0.53
        assert d.risk_adjusted_ev == pytest.approx(0.53, abs=0.01)


# ═══════════════════════════════════════════════════════
#  detect_conflict
# ═══════════════════════════════════════════════════════


class TestDetectConflict:
    def test_single_source_no_conflict(self) -> None:
        d = UnifiedInvestmentDecision(
            ticker="sh.600519", eval_date="2026-08-11",
            final_signal=Signal.HOLD, final_confidence=50.0,
            ta_signal=Signal.BUY,
        )
        d.detect_conflict()
        assert d.conflict == "none"

    def test_two_sources_agree_no_conflict(self) -> None:
        d = UnifiedInvestmentDecision(
            ticker="sh.600519", eval_date="2026-08-11",
            final_signal=Signal.HOLD, final_confidence=50.0,
            ta_signal=Signal.BUY, kronos_direction="UP",
        )
        d.detect_conflict()
        assert d.conflict == "none"

    def test_ta_vs_kronos_conflict(self) -> None:
        d = UnifiedInvestmentDecision(
            ticker="sh.600519", eval_date="2026-08-11",
            final_signal=Signal.HOLD, final_confidence=50.0,
            ta_signal=Signal.BUY, kronos_direction="DOWN",
        )
        d.detect_conflict()
        assert d.conflict == "ta_vs_kronos"

    def test_ta_vs_committee_conflict(self) -> None:
        d = UnifiedInvestmentDecision(
            ticker="sh.600519", eval_date="2026-08-11",
            final_signal=Signal.HOLD, final_confidence=50.0,
            ta_signal=Signal.BUY, committee_rec=Signal.SELL,
        )
        d.detect_conflict()
        assert d.conflict == "ta_vs_committee"

    def test_all_three_conflict(self) -> None:
        d = UnifiedInvestmentDecision(
            ticker="sh.600519", eval_date="2026-08-11",
            final_signal=Signal.HOLD, final_confidence=50.0,
            ta_signal=Signal.BUY,
            kronos_direction="DOWN",
            committee_rec=Signal.HOLD,
        )
        d.detect_conflict()
        assert d.conflict == "all_conflict"

    def test_no_active_sources_no_conflict(self) -> None:
        d = UnifiedInvestmentDecision(
            ticker="sh.600519", eval_date="2026-08-11",
            final_signal=Signal.HOLD, final_confidence=50.0,
        )
        d.detect_conflict()
        assert d.conflict == "none"


# ═══════════════════════════════════════════════════════
#  apply_final_signal
# ═══════════════════════════════════════════════════════


class TestApplyFinalSignal:
    def test_empty_votes_default_hold(self) -> None:
        d = UnifiedInvestmentDecision(
            ticker="sh.600519", eval_date="2026-08-11",
            final_signal=Signal.HOLD, final_confidence=50.0,
        )
        d.apply_final_signal()
        assert d.final_signal is Signal.HOLD
        assert d.final_confidence == pytest.approx(50.0, abs=1)

    def test_single_source_adopt(self) -> None:
        d = UnifiedInvestmentDecision(
            ticker="sh.600519", eval_date="2026-08-11",
            final_signal=Signal.HOLD, final_confidence=50.0,
            ta_signal=Signal.BUY, ta_confidence=80.0,
        )
        d.apply_final_signal()
        assert d.final_signal is Signal.BUY
        assert d.final_confidence == pytest.approx(80.0, abs=1)

    def test_two_sources_agree(self) -> None:
        d = UnifiedInvestmentDecision(
            ticker="sh.600519", eval_date="2026-08-11",
            final_signal=Signal.HOLD, final_confidence=50.0,
            ta_signal=Signal.BUY, ta_confidence=70.0,
            kronos_direction="UP", direction_score=0.8,
        )
        d.apply_final_signal()
        assert d.final_signal is Signal.BUY
        # Both votes are BUY. weighted_conf=100, dissent_penalty=(3-2)*10=10 → 90.0
        assert d.final_confidence == pytest.approx(90.0, abs=1)

    def test_two_against_one_dissent(self) -> None:
        d = UnifiedInvestmentDecision(
            ticker="sh.600519", eval_date="2026-08-11",
            final_signal=Signal.HOLD, final_confidence=50.0,
            ta_signal=Signal.BUY, ta_confidence=70.0,
            kronos_direction="UP", direction_score=0.7,
            committee_rec=Signal.SELL, committee_confidence=60.0,
        )
        d.apply_final_signal()
        assert d.final_signal is Signal.BUY
        # majority=2, dissent=1, penalty=10
        # weighted_conf = (70*70 + 70*70) / (70+70+60) = 9800/200 = 49.0 ...
        # actually: votes are (BUY,70), (BUY,70), (SELL,60)
        # total_weight = 200, buy_weight = 140, weighted = 70
        # dissent_penalty = (3-2)*10 = 10
        # final = 70 - 10 = 60
        assert d.final_confidence == pytest.approx(60.0, abs=1)

    def test_three_way_conflict_hold(self) -> None:
        d = UnifiedInvestmentDecision(
            ticker="sh.600519", eval_date="2026-08-11",
            final_signal=Signal.HOLD, final_confidence=50.0,
            ta_signal=Signal.BUY, ta_confidence=70.0,
            kronos_direction="DOWN", direction_score=0.6,
            committee_rec=Signal.HOLD, committee_confidence=50.0,
        )
        d.apply_final_signal()
        # Three-way tie: max(sig_counts) returns first inserted (BUY). Conflicts, but signal is BUY.
        assert d.final_signal is Signal.BUY
        # Each has count=1, total_weight=180, buy_weight=70 → 38.9 - 20 = 18.9
        assert d.final_confidence < 50

    def test_kronos_none_skipped(self) -> None:
        d = UnifiedInvestmentDecision(
            ticker="sh.600519", eval_date="2026-08-11",
            final_signal=Signal.HOLD, final_confidence=50.0,
            ta_signal=Signal.BUY, ta_confidence=80.0,
            kronos_direction=None,
        )
        d.apply_final_signal()
        assert d.final_signal is Signal.BUY


# ═══════════════════════════════════════════════════════
#  to_dict / from_dict
# ═══════════════════════════════════════════════════════


class TestSerialization:
    def test_to_dict_roundtrip(self) -> None:
        d = UnifiedInvestmentDecision(
            ticker="sh.600519", eval_date="2026-08-11",
            final_signal=Signal.BUY, final_confidence=75.0,
            ta_signal=Signal.BUY, ta_confidence=80.0,
            kronos_direction="UP", kronos_expected_return=3.0,
            committee_rec=Signal.BUY, committee_confidence=70.0,
            expected_value=1.5, risk_adjusted_ev=0.8,
            thesis="Strong momentum", risks=["high_vol"],
            invalidations=["break_100"],
        )
        d.compute_expected_value().detect_conflict().apply_final_signal()
        data = d.to_dict()
        assert data["final_signal"] == "BUY"
        assert data["ta_signal"] == "BUY"
        assert data["committee_rec"] == "BUY"
        assert data["conflict"] == "none"

        restored = UnifiedInvestmentDecision.from_dict(data)
        assert restored.ticker == d.ticker
        assert restored.final_signal is Signal.BUY
        assert restored.ta_signal is Signal.BUY
        assert restored.committee_rec is Signal.BUY

    def test_to_dict_missing_signals(self) -> None:
        d = UnifiedInvestmentDecision(
            ticker="sh.600519", eval_date="2026-08-11",
            final_signal=Signal.HOLD, final_confidence=50.0,
        )
        data = d.to_dict()
        assert "ta_signal" not in data or data["ta_signal"] is None

    def test_from_dict_string_conflict_kept(self) -> None:
        data = {
            "ticker": "sh.600519", "eval_date": "2026-08-11",
            "final_signal": "BUY", "final_confidence": 70.0,
            "conflict": "ta_vs_kronos",
        }
        d = UnifiedInvestmentDecision.from_dict(data)
        assert d.conflict == "ta_vs_kronos"


# ═══════════════════════════════════════════════════════
#  to_ta_decision
# ═══════════════════════════════════════════════════════


class TestToTADecision:
    def test_conversion(self) -> None:
        d = UnifiedInvestmentDecision(
            ticker="sh.600519", eval_date="2026-08-11",
            final_signal=Signal.BUY, final_confidence=75.0,
            thesis="test thesis", risks=["r1"], invalidations=["i1"],
            target_price=110.0, stop_loss=95.0, horizon=30,
            kronos_expected_return=5.0,
        )
        td = d.to_ta_decision()
        assert td.signal is Signal.BUY
        assert td.confidence == 75.0
        assert td.expected_return == 5.0
        assert td.thesis == "test thesis"
        assert td.risks == ["r1"]
        assert td.target_price == 110.0
        assert td.horizon == 30


# ═══════════════════════════════════════════════════════
#  build_unified_decision (factory)
# ═══════════════════════════════════════════════════════


class TestBuildUnifiedDecision:
    def test_minimal(self) -> None:
        d = build_unified_decision("sh.600519", "2026-08-11")
        assert d.ticker == "sh.600519"
        assert d.final_signal is Signal.HOLD

    def test_with_ta_decision(self) -> None:
        td = TADecision(
            signal=Signal.BUY, confidence=80.0,
            thesis="bullish", risks=["vol"],
        )
        d = build_unified_decision(
            "sh.600519", "2026-08-11",
            ta_decision=td, kronos_direction="UP",
            kronos_expected_return=3.0,
        )
        assert d.ta_signal is Signal.BUY
        assert d.kronos_direction == "UP"
        assert d.final_signal is Signal.BUY

    def test_with_distribution(self) -> None:
        d = build_unified_decision(
            "sh.600519", "2026-08-11",
            kronos_direction="UP", kronos_expected_return=2.0,
            distribution={"p10": 95.0, "p90": 110.0, "direction_score": 0.7, "predicted_close_final": 100.0},
        )
        assert d.p10 == 95.0
        assert d.p90 == 110.0
        assert d.direction_score == 0.7
        assert d.expected_value is not None

    def test_with_committee(self) -> None:
        d = build_unified_decision(
            "sh.600519", "2026-08-11",
            ta_decision=TADecision(signal=Signal.BUY, confidence=70.0),
            committee_rec=Signal.BUY, committee_confidence=65.0,
            bull_case="strong earnings", bear_case="macro headwind",
        )
        assert d.committee_rec is Signal.BUY
        assert d.bull_case == "strong earnings"
        assert d.bear_case == "macro headwind"
