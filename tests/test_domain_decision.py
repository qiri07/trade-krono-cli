"""测试 InvestmentDecision 领域对象的序列化/反序列化/边界行为。"""

from __future__ import annotations

from trade_krono_cli.domain.decision import InvestmentDecision
from trade_krono_cli.domain.prediction import KronosPrediction, TAAnalysis
from trade_krono_cli.domain.risk import RiskAssessment
from trade_krono_cli.domain.signal import SignalAssessment
from trade_krono_cli.domain.types import Direction, Signal


class TestInvestmentDecisionFromDict:
    """InvestmentDecision.from_dict 测试。"""

    def test_from_dict_basic(self) -> None:
        data = {
            "ticker": "sh.600519",
            "eval_date": "2026-09-01",
            "signal": "BUY",
            "confidence": 85.0,
        }
        d = InvestmentDecision.from_dict(data)
        assert d.ticker == "sh.600519"
        assert d.signal == Signal.BUY
        assert d.confidence == 85.0
        assert d.expected_value is None
        assert d.ranking_score is None

    def test_from_dict_invalid_signal_fallback(self) -> None:
        """无效 signal 字符串回退到 HOLD。"""
        data = {"ticker": "sh.600519", "signal": "INVALID_SIGNAL", "confidence": 50.0}
        d = InvestmentDecision.from_dict(data)
        assert d.signal == Signal.HOLD

    def test_from_dict_composite_score_backward_compat(self) -> None:
        """旧版 composite_score 字段名兼容。"""
        data = {
            "ticker": "sh.600519",
            "signal": "BUY",
            "confidence": 60.0,
            "composite_score": 72.5,
        }
        d = InvestmentDecision.from_dict(data)
        assert d.ranking_score == 72.5

    def test_from_dict_ranking_score_explicit(self) -> None:
        """新版 ranking_score 字段名直接生效。"""
        data = {
            "ticker": "sh.600519",
            "signal": "BUY",
            "confidence": 60.0,
            "ranking_score": 88.0,
        }
        d = InvestmentDecision.from_dict(data)
        assert d.ranking_score == 88.0

    def test_from_dict_with_signal_assessment(self) -> None:
        data = {
            "ticker": "sh.600519",
            "eval_date": "2026-09-01",
            "signal": "BUY",
            "confidence": 80.0,
            "signal_assessment": {
                "ticker": "sh.600519",
                "eval_date": "2026-09-01",
                "final_signal": "BUY",
                "final_confidence": 80.0,
                "conflict": "none",
            },
        }
        d = InvestmentDecision.from_dict(data)
        assert d.signal_assessment is not None
        assert d.signal_assessment.ticker == "sh.600519"

    def test_from_dict_with_risk_assessment(self) -> None:
        data = {
            "ticker": "sh.600519",
            "signal": "HOLD",
            "confidence": 50.0,
            "risk_assessment": {
                "ticker": "sh.600519",
                "eval_date": "2026-09-01",
                "risk_score_total": 0.35,
                "adjusted_expected_return": 0.02,
            },
        }
        d = InvestmentDecision.from_dict(data)
        assert d.risk_assessment is not None
        assert d.risk_assessment.risk_score_total == 0.35

    def test_from_dict_with_all_fields(self) -> None:
        data = {
            "ticker": "sz.000858",
            "eval_date": "2026-09-01",
            "signal": "OVERWEIGHT",
            "confidence": 75.0,
            "expected_value": 3.5,
            "prob_win": 0.65,
            "risk_adjusted_ev": 1.2,
            "ranking_score": 88.0,
            "position_size": 0.25,
            "entry_zone": [170.0, 180.0],
            "target_price": 200.0,
            "stop_loss": 160.0,
            "horizon": 10,
            "thesis": "基本面稳健，技术面突破",
            "risks": ["市场波动", "政策风险"],
            "invalidations": ["跌破160元"],
            "job_id": "job-001",
        }
        d = InvestmentDecision.from_dict(data)
        assert d.expected_value == 3.5
        assert d.prob_win == 0.65
        assert d.position_size == 0.25
        assert d.entry_zone == [170.0, 180.0]
        assert d.target_price == 200.0
        assert d.stop_loss == 160.0
        assert d.horizon == 10
        assert d.thesis == "基本面稳健，技术面突破"
        assert d.risks == ["市场波动", "政策风险"]
        assert d.invalidations == ["跌破160元"]
        assert d.job_id == "job-001"

    def test_from_dict_missing_required_ticker_raises(self) -> None:
        data = {"signal": "BUY", "confidence": 50.0}
        try:
            InvestmentDecision.from_dict(data)
            assert False, "Expected KeyError"
        except KeyError:
            pass


class TestInvestmentDecisionFallback:
    """InvestmentDecision.fallback 测试。"""

    def test_fallback_default(self) -> None:
        d = InvestmentDecision.fallback("sh.600519", "2026-09-01")
        assert d.signal == Signal.HOLD
        assert d.confidence == 50.0
        assert d.ticker == "sh.600519"
        assert d.eval_date == "2026-09-01"

    def test_fallback_custom_signal(self) -> None:
        d = InvestmentDecision.fallback("sz.000001", "2026-08-01", signal=Signal.SELL, confidence=30.0)
        assert d.signal == Signal.SELL
        assert d.confidence == 30.0


class TestInvestmentDecisionToLegacyDict:
    """InvestmentDecision.to_legacy_dict 测试。"""

    def test_to_legacy_dict_minimal(self) -> None:
        d = InvestmentDecision(
            ticker="sh.600519",
            eval_date="2026-09-01",
            signal=Signal.BUY,
            confidence=80.0,
        )
        legacy = d.to_legacy_dict()
        assert legacy["ticker"] == "sh.600519"
        assert legacy["signal"] == "BUY"
        assert legacy["composite_score"] is None
        assert "ta_signal" not in legacy
        assert "kronos_direction" not in legacy

    def test_to_legacy_dict_with_signal_assessment(self) -> None:
        sa = SignalAssessment(
            ticker="sh.600519",
            eval_date="2026-09-01",
            ta=TAAnalysis(
                ticker="sh.600519",
                eval_date="2026-09-01",
                signal="BUY",
                confidence=80.0,
            ),
            final_signal=Signal.BUY,
            final_confidence=80.0,
        )
        d = InvestmentDecision(
            ticker="sh.600519",
            eval_date="2026-09-01",
            signal=Signal.BUY,
            confidence=80.0,
            signal_assessment=sa,
        )
        legacy = d.to_legacy_dict()
        assert legacy["ta_signal"] == "BUY"
        assert legacy["ta_confidence"] == 80.0
        assert legacy["kronos_direction"] is None

    def test_to_legacy_dict_with_risk_assessment(self) -> None:
        ra = RiskAssessment(
            ticker="sh.600519",
            eval_date="2026-09-01",
            risk_score_total=0.35,
            adjusted_expected_return=0.02,
        )
        d = InvestmentDecision(
            ticker="sh.600519",
            eval_date="2026-09-01",
            signal=Signal.HOLD,
            confidence=50.0,
            risk_assessment=ra,
        )
        legacy = d.to_legacy_dict()
        assert legacy["risk_score_total"] == 0.35
        assert legacy["adjusted_expected_return"] == 0.02

    def test_to_dict_with_assessments(self) -> None:
        sa = SignalAssessment(
            ticker="sh.600519", eval_date="2026-09-01",
            ta=TAAnalysis(ticker="sh.600519", eval_date="2026-09-01", signal="BUY", confidence=80.0),
            final_signal=Signal.BUY, final_confidence=80.0,
        )
        ra = RiskAssessment(
            ticker="sh.600519", eval_date="2026-09-01",
            risk_score_total=0.35, adjusted_expected_return=0.02,
        )
        d = InvestmentDecision(
            ticker="sh.600519", eval_date="2026-09-01",
            signal=Signal.BUY, confidence=80.0,
            signal_assessment=sa, risk_assessment=ra,
        )
        data = d.to_dict()
        assert "signal_assessment" in data
        assert "risk_assessment" in data
        assert data["signal_assessment"]["final_signal"] == "BUY"
        assert data["risk_assessment"]["risk_score_total"] == 0.35

    def test_to_legacy_dict_with_kronos_distribution(self) -> None:
        from trade_krono_cli.domain.prediction import PredictionDistribution

        dist = PredictionDistribution(expected_return=2.5, direction=Direction.UP, p10=-1.0, p90=6.0)
        kr = KronosPrediction(
            ticker="sh.600519", eval_date="2026-09-01", horizon=5,
            direction=Direction.UP, expected_return=2.5, predicted_close=180.0, distribution=dist,
        )
        sa = SignalAssessment(
            ticker="sh.600519", eval_date="2026-09-01",
            kronos=kr, final_signal=Signal.BUY, final_confidence=75.0,
        )
        d = InvestmentDecision(
            ticker="sh.600519", eval_date="2026-09-01",
            signal=Signal.BUY, confidence=75.0,
            signal_assessment=sa,
        )
        legacy = d.to_legacy_dict()
        assert legacy["kronos_direction"] == "UP"
        assert legacy["kronos_change_pct"] == 2.5
        assert "kronos_prediction_uncertainty" in legacy
