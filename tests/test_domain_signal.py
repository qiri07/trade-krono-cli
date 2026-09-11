"""测试 domain/signal.py 中的 SignalAssessment 序列化与辅助函数。"""

from __future__ import annotations

from trade_krono_cli.domain.prediction import KronosPrediction, TAAnalysis
from trade_krono_cli.domain.signal import (
    SignalAssessment,
    SignalConflict,
    _compute_ev,
    detect_conflict,
)
from trade_krono_cli.domain.types import Direction
from trade_krono_cli.domain.types import Signal as DomainSignal


class TestSignalConflict:
    """SignalConflict 静态方法测试。"""

    def test_is_conflict_none(self) -> None:
        assert SignalConflict.is_conflict(SignalConflict.NONE) is False

    def test_is_conflict_values(self) -> None:
        assert SignalConflict.is_conflict(SignalConflict.TA_vs_KRONOS) is True
        assert SignalConflict.is_conflict(SignalConflict.TA_vs_COMMITTEE) is True
        assert SignalConflict.is_conflict(SignalConflict.KRONOS_vs_COMMITTEE) is True
        assert SignalConflict.is_conflict(SignalConflict.ALL_CONFLICT) is True

    def test_is_conflict_unknown_string(self) -> None:
        assert SignalConflict.is_conflict("random_string") is False


class TestSignalAssessmentFromDict:
    """SignalAssessment.from_dict 测试。"""

    def test_from_dict_basic(self) -> None:
        data = {
            "ticker": "sh.600519",
            "eval_date": "2026-09-01",
            "final_signal": "BUY",
            "final_confidence": 80.0,
            "conflict": "none",
        }
        sa = SignalAssessment.from_dict(data)
        assert sa.ticker == "sh.600519"
        assert sa.final_signal == DomainSignal.BUY
        assert sa.final_confidence == 80.0
        assert sa.conflict == SignalConflict.NONE

    def test_from_dict_invalid_final_signal_fallback(self) -> None:
        data = {
            "ticker": "sh.600519",
            "final_signal": "INVALID",
            "final_confidence": 50.0,
        }
        sa = SignalAssessment.from_dict(data)
        assert sa.final_signal == DomainSignal.HOLD

    def test_from_dict_invalid_committee_signal_fallback(self) -> None:
        data = {
            "ticker": "sh.600519",
            "final_signal": "BUY",
            "committee_rec": "BAD_SIGNAL",
        }
        sa = SignalAssessment.from_dict(data)
        assert sa.committee_rec is None

    def test_from_dict_with_ta_and_kronos(self) -> None:
        ta_data = {
            "ticker": "sh.600519",
            "eval_date": "2026-09-01",
            "signal": "BUY",
            "confidence": 75.0,
        }
        kr_data = {
            "ticker": "sh.600519",
            "eval_date": "2026-09-01",
            "direction": "UP",
            "expected_return": 2.5,
            "p10": -1.0,
            "p90": 6.0,
        }
        data = {
            "ticker": "sh.600519",
            "eval_date": "2026-09-01",
            "final_signal": "BUY",
            "final_confidence": 80.0,
            "ta_analysis": ta_data,
            "kronos_prediction": kr_data,
        }
        sa = SignalAssessment.from_dict(data)
        assert sa.ta is not None
        assert sa.ta.signal == "BUY"
        assert sa.kronos is not None
        assert sa.kronos.direction == Direction.UP

    def test_from_dict_with_committee_rec(self) -> None:
        data = {
            "ticker": "sh.600519",
            "final_signal": "BUY",
            "committee_rec": "OVERWEIGHT",
            "committee_confidence": 70.0,
        }
        sa = SignalAssessment.from_dict(data)
        assert sa.committee_rec == DomainSignal.OVERWEIGHT
        assert sa.committee_confidence == 70.0

    def test_from_dict_committee_rec_as_enum(self) -> None:
        """committee_rec 直接传入枚举值时不走字符串解析路径。"""
        data = {
            "ticker": "sh.600519",
            "final_signal": "BUY",
            "committee_rec": DomainSignal.SELL,
            "committee_confidence": 60.0,
        }
        sa = SignalAssessment.from_dict(data)
        assert sa.committee_rec == DomainSignal.SELL
        assert sa.committee_confidence == 60.0

    def test_from_dict_with_bull_bear_case(self) -> None:
        data = {
            "ticker": "sh.600519",
            "final_signal": "BUY",
            "bull_case": "盈利增长",
            "bear_case": "宏观下行",
        }
        sa = SignalAssessment.from_dict(data)
        assert sa.bull_case == "盈利增长"
        assert sa.bear_case == "宏观下行"

    def test_from_dict_with_trade_params(self) -> None:
        data = {
            "ticker": "sh.600519",
            "final_signal": "BUY",
            "position_size": 0.2,
            "entry_zone": [170.0, 180.0],
            "target_price": 200.0,
            "stop_loss": 160.0,
            "horizon": 10,
        }
        sa = SignalAssessment.from_dict(data)
        assert sa.position_size == 0.2
        assert sa.entry_zone == [170.0, 180.0]
        assert sa.target_price == 200.0
        assert sa.stop_loss == 160.0
        assert sa.horizon == 10

    def test_from_dict_missing_ticker_raises(self) -> None:
        data = {"final_signal": "BUY"}
        try:
            SignalAssessment.from_dict(data)
            assert False, "Expected KeyError"
        except KeyError:
            pass


class TestSignalAssessmentToDict:
    """SignalAssessment.to_dict 测试。"""

    def test_to_dict_basic(self) -> None:
        sa = SignalAssessment(ticker="sh.600519", eval_date="2026-09-01")
        d = sa.to_dict()
        assert d["ticker"] == "sh.600519"
        assert d["final_signal"] == "HOLD"
        assert d["conflict"] == SignalConflict.NONE
        assert "ta_analysis" not in d
        assert "kronos_prediction" not in d

    def test_to_dict_with_ta(self) -> None:
        ta = TAAnalysis(ticker="sh.600519", eval_date="2026-09-01", signal="BUY", confidence=80.0)
        sa = SignalAssessment(ticker="sh.600519", eval_date="2026-09-01", ta=ta)
        d = sa.to_dict()
        assert "ta_analysis" in d
        assert d["ta_analysis"]["signal"] == "BUY"

    def test_to_dict_with_kronos(self) -> None:
        from trade_krono_cli.domain.prediction import PredictionDistribution

        dist = PredictionDistribution(
            expected_return=2.5, direction=Direction.UP, p10=-1.0, p90=6.0
        )
        kr = KronosPrediction(
            ticker="sh.600519",
            eval_date="2026-09-01",
            horizon=5,
            direction=Direction.UP,
            expected_return=2.5,
            predicted_close=180.0,
            distribution=dist,
        )
        sa = SignalAssessment(ticker="sh.600519", eval_date="2026-09-01", kronos=kr)
        d = sa.to_dict()
        assert "kronos_prediction" in d
        assert d["kronos_prediction"]["direction"] == "UP"

    def test_to_dict_with_committee_and_params(self) -> None:
        sa = SignalAssessment(
            ticker="sh.600519",
            eval_date="2026-09-01",
            final_signal=DomainSignal.BUY,
            committee_rec=DomainSignal.OVERWEIGHT,
            committee_confidence=70.0,
            bull_case="盈利增长",
            bear_case="宏观下行",
            entry_zone=[170.0, 180.0],
            target_price=200.0,
            stop_loss=160.0,
        )
        d = sa.to_dict()
        assert d["committee_rec"] == "OVERWEIGHT"
        assert d["committee_confidence"] == 70.0
        assert d["bull_case"] == "盈利增长"
        assert d["bear_case"] == "宏观下行"
        assert d["entry_zone"] == [170.0, 180.0]
        assert d["target_price"] == 200.0
        assert d["stop_loss"] == 160.0

    def test_to_dict_with_all_optional_fields(self) -> None:
        from trade_krono_cli.domain.prediction import PredictionDistribution

        dist = PredictionDistribution(
            expected_return=2.5, direction=Direction.UP, p10=-1.0, p90=6.0
        )
        kr = KronosPrediction(
            ticker="sh.600519",
            eval_date="2026-09-01",
            horizon=5,
            direction=Direction.UP,
            expected_return=2.5,
            predicted_close=180.0,
            distribution=dist,
        )
        ta = TAAnalysis(ticker="sh.600519", eval_date="2026-09-01", signal="BUY", confidence=80.0)
        sa = SignalAssessment(
            ticker="sh.600519",
            eval_date="2026-09-01",
            ta=ta,
            kronos=kr,
            committee_rec=DomainSignal.OVERWEIGHT,
            committee_confidence=70.0,
            bull_case="bull",
            bear_case="bear",
            entry_zone=[170.0, 180.0],
            target_price=200.0,
            stop_loss=160.0,
            thesis="test thesis",
            risks=["r1"],
            invalidations=["inv1"],
        )
        d = sa.to_dict()
        assert "ta_analysis" in d
        assert "kronos_prediction" in d
        assert d["committee_rec"] == "OVERWEIGHT"
        assert d["bull_case"] == "bull"
        assert d["bear_case"] == "bear"
        assert d["thesis"] == "test thesis"
        assert d["risks"] == ["r1"]

    def test_from_dict_with_ta_kronos_roundtrip(self) -> None:
        ta_data = {
            "ticker": "sh.600519",
            "eval_date": "2026-09-01",
            "signal": "BUY",
            "confidence": 75.0,
        }
        kr_data = {
            "ticker": "sh.600519",
            "eval_date": "2026-09-01",
            "direction": "UP",
            "expected_return": 2.5,
            "p10": -1.0,
            "p90": 6.0,
        }
        data = {
            "ticker": "sh.600519",
            "eval_date": "2026-09-01",
            "final_signal": "BUY",
            "final_confidence": 80.0,
            "ta_analysis": ta_data,
            "kronos_prediction": kr_data,
        }
        sa = SignalAssessment.from_dict(data)
        d = sa.to_dict()
        assert d["ta_analysis"]["signal"] == "BUY"
        assert d["kronos_prediction"]["direction"] == "UP"


class TestComputeEV:
    """_compute_ev 辅助函数测试。"""

    def test_none_return(self) -> None:
        result = _compute_ev(Direction.UP, None, None, None, 17.0)
        assert result == (None, None, None, None, None, None)

    def test_positive_return(self) -> None:
        prob_win, prob_loss, avg_win, avg_loss, ev, raev = _compute_ev(
            Direction.UP, 3.0, None, None, 17.0
        )
        assert prob_win is not None
        assert prob_loss is not None
        assert abs(prob_win + prob_loss - 1.0) < 1e-6
        assert avg_win > 0
        assert ev is not None

    def test_negative_return(self) -> None:
        prob_win, prob_loss, avg_win, avg_loss, ev, raev = _compute_ev(
            Direction.DOWN, -2.0, None, None, 17.0
        )
        assert prob_win < 0.5
        assert avg_loss > 0

    def test_explicit_quantiles(self) -> None:
        prob_win, prob_loss, avg_win, avg_loss, ev, raev = _compute_ev(
            Direction.UP, 3.0, p10=-1.0, p90=6.0, cost_bps=17.0
        )
        assert prob_win is not None
        assert ev is not None

    def test_zero_vol_proxy_returns_nonzero_raev(self) -> None:
        """当 p10==p90 时，vol_proxy = abs(ret)*0.5，raev 非零。"""
        prob_win, prob_loss, avg_win, avg_loss, ev, raev = _compute_ev(
            Direction.UP, 3.0, p10=3.0, p90=3.0, cost_bps=17.0
        )
        # vol_proxy = abs(3.0) * 0.5 = 1.5, raev = ev / 1.5 != 0
        assert raev != 0.0


class TestDetectConflict:
    """detect_conflict 辅助函数测试。"""

    def test_all_none(self) -> None:
        assert detect_conflict(None, None, None) == SignalConflict.NONE

    def test_single_source(self) -> None:
        assert detect_conflict(DomainSignal.BUY, None, None) == SignalConflict.NONE
        assert detect_conflict(None, Direction.UP, None) == SignalConflict.NONE
        assert detect_conflict(None, None, DomainSignal.BUY) == SignalConflict.NONE

    def test_two_same_signals(self) -> None:
        assert detect_conflict(DomainSignal.BUY, Direction.UP, None) == SignalConflict.NONE
        assert detect_conflict(DomainSignal.BUY, None, DomainSignal.BUY) == SignalConflict.NONE

    def test_ta_vs_kronos_conflict(self) -> None:
        result = detect_conflict(DomainSignal.BUY, Direction.DOWN, None)
        assert result == "ta_vs_kronos"

    def test_ta_vs_committee_conflict(self) -> None:
        result = detect_conflict(DomainSignal.BUY, None, DomainSignal.SELL)
        assert result == "ta_vs_committee"

    def test_kronos_vs_committee_conflict(self) -> None:
        result = detect_conflict(None, Direction.UP, DomainSignal.SELL)
        assert result == "kronos_vs_committee"

    def test_all_agree_buy(self) -> None:
        result = detect_conflict(DomainSignal.BUY, Direction.UP, DomainSignal.BUY)
        assert result == SignalConflict.NONE

    def test_kronos_flat_maps_to_hold(self) -> None:
        result = detect_conflict(DomainSignal.BUY, Direction.FLAT, None)
        assert result == "ta_vs_kronos"
