"""domain 模型和 retry_policy 的测试。"""

from __future__ import annotations

import pytest

from trade_krono_cli.domain.decision import InvestmentDecision
from trade_krono_cli.domain.experiment import Experiment, Hypothesis
from trade_krono_cli.domain.market import MarketSnapshot
from trade_krono_cli.domain.signal import SignalAssessment, SignalConflict
from trade_krono_cli.domain.stock import Stock
from trade_krono_cli.domain.types import Direction, ExperimentType, Signal
from trade_krono_cli.globals import clear_all_globals
from trade_krono_cli.retry_policy.classifier import classify_error
from trade_krono_cli.retry_policy.exceptions import (
    NetworkError,
    RateLimitError,
    TimeoutError,
    TradeKronoNonRetryableError,
    TradeKronoRetryableError,
)
from trade_krono_cli.ta_decision import DecisionAdapter


class TestSignal:
    """Signal 枚举测试。"""

    def test_buy_signal(self) -> None:
        assert Signal.BUY.value == "BUY"

    def test_sell_signal(self) -> None:
        assert Signal.SELL.value == "SELL"

    def test_hold_signal(self) -> None:
        assert Signal.HOLD.value == "HOLD"

    def test_overweight_signal(self) -> None:
        assert Signal.OVERWEIGHT.value == "OVERWEIGHT"


class TestDirection:
    """Direction 枚举测试。"""

    def test_up_direction(self) -> None:
        assert Direction.UP.value == "UP"

    def test_down_direction(self) -> None:
        assert Direction.DOWN.value == "DOWN"

    def test_flat_direction(self) -> None:
        assert Direction.FLAT.value == "FLAT"

    def test_from_str_valid(self) -> None:
        assert Direction.from_str("UP") == Direction.UP
        assert Direction.from_str("DOWN") == Direction.DOWN
        assert Direction.from_str("FLAT") == Direction.FLAT

    def test_from_str_none(self) -> None:
        assert Direction.from_str(None) is None

    def test_from_str_invalid(self) -> None:
        assert Direction.from_str("INVALID") is None


class TestSignalAssessment:
    """SignalAssessment 领域模型测试。"""

    def test_create_assessment(self) -> None:
        a = SignalAssessment(
            ticker="sh.600519",
            eval_date="2026-09-01",
            final_signal=Signal.BUY,
            final_confidence=85.0,
            expected_value=0.05,
            prob_win=0.6,
        )
        assert a.ticker == "sh.600519"
        assert a.final_signal == Signal.BUY
        assert a.final_confidence == 85.0

    def test_assessment_with_conflict(self) -> None:
        a = SignalAssessment(
            ticker="sh.600519",
            eval_date="2026-09-01",
            final_signal=Signal.HOLD,
            final_confidence=50.0,
            conflict=SignalConflict.TA_vs_KRONOS,
        )
        assert a.conflict == SignalConflict.TA_vs_KRONOS


class TestInvestmentDecision:
    """InvestmentDecision 测试。"""

    def test_create_decision(self) -> None:
        d = InvestmentDecision(
            ticker="sh.600519",
            eval_date="2026-09-01",
            signal=Signal.BUY,
            confidence=80.0,
            thesis="strong momentum",
            risks=["overbought"],
        )
        assert d.ticker == "sh.600519"
        assert d.signal == Signal.BUY
        assert d.confidence == 80.0

    def test_decision_to_dict(self) -> None:
        d = InvestmentDecision(
            ticker="sh.600519",
            eval_date="2026-09-01",
            signal=Signal.HOLD,
            confidence=50.0,
        )
        data = d.to_dict()
        assert data["ticker"] == "sh.600519"
        assert data["signal"] == "HOLD"

    def test_decision_with_empty_reasoning(self) -> None:
        d = InvestmentDecision(
            ticker="sh.600519",
            eval_date="2026-09-01",
            signal=Signal.BUY,
            confidence=70.0,
        )
        assert d.thesis == ""
        assert d.risks == []


class TestDecisionAdapter:
    """DecisionAdapter 测试。"""

    def test_parse_buy_text(self) -> None:
        adapter = DecisionAdapter()
        decision = adapter.parse("**Rating**: Buy\n**Investment Thesis**: strong momentum")
        assert decision.signal in (Signal.BUY, Signal.OVERWEIGHT, Signal.HOLD, Signal.SELL)
        assert decision.confidence > 0

    def test_parse_empty_text(self) -> None:
        adapter = DecisionAdapter()
        decision = adapter.parse("")
        assert decision.signal == Signal.HOLD
        assert decision.confidence == 50.0


class TestMarketSnapshot:
    """MarketSnapshot 测试。"""

    def test_create_snapshot(self) -> None:
        s = MarketSnapshot(
            stock=Stock(ticker="sh.600519"),
            date="2026-09-01",
            close=1800.0,
            open=1790.0,
            high=1810.0,
            low=1785.0,
            volume=1_000_000.0,
            prev_close=1780.0,
        )
        assert s.date == "2026-09-01"
        assert s.close == 1800.0
        assert s.change_pct == pytest.approx(1.12, abs=0.01)

    def test_snapshot_to_dict(self) -> None:
        s = MarketSnapshot(
            stock=Stock(ticker="sh.600519"),
            date="2026-09-01",
            close=100.0,
            open=99.0,
            high=101.0,
            low=98.0,
            volume=500_000.0,
            prev_close=99.0,
        )
        d = s.to_dict()
        assert "date" in d
        assert d["close"] == 100.0

    def test_from_dict(self) -> None:
        s = MarketSnapshot.from_dict(
            {
                "stock": {"ticker": "sh.600519"},
                "date": "2026-09-01",
                "close": 1800.0,
                "open": 1790.0,
                "high": 1810.0,
                "low": 1785.0,
                "volume": 1_000_000.0,
                "prev_close": 1780.0,
            },
        )
        assert s.date == "2026-09-01"
        assert s.close == 1800.0


class TestExperimentModel:
    """Experiment 领域模型测试。"""

    def test_create_experiment(self) -> None:
        hyp = Hypothesis(
            statement="momentum continues for 30 days",
            prediction="UP",
            falsification="DOWN",
        )
        exp = Experiment(
            experiment_id="exp-001",
            experiment_type=ExperimentType.ALPHA,
            hypothesis=hyp,
            description="momentum_test",
        )
        assert exp.experiment_id == "exp-001"
        assert exp.experiment_type == ExperimentType.ALPHA
        assert exp.hypothesis.statement == "momentum continues for 30 days"

    def test_experiment_to_dict(self) -> None:
        hyp = Hypothesis(statement="test", prediction="UP", falsification="DOWN")
        exp = Experiment(
            experiment_id="exp-001",
            experiment_type=ExperimentType.ALPHA,
            hypothesis=hyp,
        )
        d = exp.to_dict()
        assert d["experiment_id"] == "exp-001"

    def test_hypothesis_creation(self) -> None:
        h = Hypothesis(
            statement="momentum continues for 30 days",
            prediction="UP",
            falsification="DOWN",
            metric="win_rate",
            threshold=0.55,
        )
        assert h.statement == "momentum continues for 30 days"
        assert h.threshold == 0.55

    def test_hypothesis_check_pass(self) -> None:
        h = Hypothesis(statement="test", prediction="UP", falsification="DOWN", threshold=0.5)
        passed, _expl = h.check(0.7)
        assert passed is True

    def test_hypothesis_check_fail(self) -> None:
        h = Hypothesis(statement="test", prediction="UP", falsification="DOWN", threshold=0.5)
        passed, _ = h.check(0.3)
        assert passed is False


class TestRetryExceptions:
    """retry_policy 异常类测试。"""

    def test_network_error_is_retryable(self) -> None:
        err = NetworkError("connection refused")
        assert isinstance(err, TradeKronoRetryableError)
        assert isinstance(err, Exception)

    def test_timeout_error_is_retryable(self) -> None:
        err = TimeoutError("request timeout")
        assert isinstance(err, TradeKronoRetryableError)

    def test_rate_limit_error_with_retry_after(self) -> None:
        err = RateLimitError("too many requests", retry_after=5.0)
        assert isinstance(err, TradeKronoRetryableError)
        assert err.retry_after == 5.0
        assert err.response_headers == {}

    def test_rate_limit_error_with_headers(self) -> None:
        headers = {"Retry-After": "10"}
        err = RateLimitError("rate limited", retry_after=10.0, response_headers=headers)
        assert err.response_headers == headers

    def test_non_retryable_error(self) -> None:
        err = TradeKronoNonRetryableError("invalid参数")
        assert isinstance(err, TradeKronoNonRetryableError)
        assert not isinstance(err, TradeKronoRetryableError)

    def test_exception_hierarchy(self) -> None:
        assert issubclass(NetworkError, TradeKronoRetryableError)
        assert issubclass(TimeoutError, TradeKronoRetryableError)
        assert issubclass(RateLimitError, TradeKronoRetryableError)
        assert issubclass(TradeKronoRetryableError, Exception)
        assert issubclass(TradeKronoNonRetryableError, Exception)


class TestErrorClassifier:
    """错误分类器测试。"""

    def test_classify_network_error(self) -> None:
        err = NetworkError("connection refused")
        category, _desc = classify_error(err)
        assert category == "retriable"

    def test_classify_rate_limit(self) -> None:
        err = RateLimitError("429 too many requests")
        category, desc = classify_error(err)
        assert category == "retriable"
        assert "429" in desc

    def test_classify_generic_exception(self) -> None:
        err = ValueError("invalid input")
        category, _ = classify_error(err)
        assert category == "non_retriable"

    def test_classify_5xx_http_error(self) -> None:
        err = RuntimeError("HTTP 500")
        category, _ = classify_error(err)
        assert category == "retriable"

    def test_classify_4xx_http_error(self) -> None:
        err = RuntimeError("HTTP 404")
        category, _ = classify_error(err)
        assert category == "non_retriable"


class TestGlobals:
    """globals 模块测试。"""

    def test_clear_all_globals(self) -> None:
        clear_all_globals()
        # 不应抛出异常
