"""Domain Prediction 数据类单元测试。

覆盖：
  - Direction 枚举
  - PredictionDistribution 序列化/反序列化
  - TAAnalysis 字段和 from_dict/failed
  - KronosPrediction 字段和属性代理
  - EvalRecord 字段
"""

from __future__ import annotations

import pytest

from trade_krono_cli.domain.prediction import (
    Direction,
    KronosPrediction,
    PredictionDistribution,
    TAAnalysis,
)
from trade_krono_cli.domain.types import Signal

# ── Signal ────────────────────────────────────────────────────────────────────


class TestSignal:
    def test_members(self) -> None:
        assert Signal.BUY.value == "BUY"
        assert Signal.OVERWEIGHT.value == "OVERWEIGHT"
        assert Signal.HOLD.value == "HOLD"
        assert Signal.SELL.value == "SELL"

    def test_from_string(self) -> None:
        assert Signal("BUY") == Signal.BUY
        assert Signal("HOLD") == Signal.HOLD

    def test_invalid_string_raises(self) -> None:
        with pytest.raises(ValueError):
            Signal("INVALID")


# ── Direction ─────────────────────────────────────────────────────────────────


class TestDirection:
    def test_members(self) -> None:
        assert Direction.UP.value == "UP"
        assert Direction.DOWN.value == "DOWN"
        assert Direction.FLAT.value == "FLAT"

    def test_from_string(self) -> None:
        assert Direction("UP") == Direction.UP
        assert Direction("DOWN") == Direction.DOWN
        assert Direction("FLAT") == Direction.FLAT

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError):
            Direction("SIDEWAYS")


# ── PredictionDistribution ────────────────────────────────────────────────────


class TestPredictionDistribution:
    def test_default_empty(self) -> None:
        d = PredictionDistribution()
        assert d.expected_return is None
        assert d.direction is None
        assert d.sample_count_used == 1

    def test_full_initialization(self) -> None:
        d = PredictionDistribution(
            expected_return=2.5,
            direction=Direction.UP,
            direction_score=75.0,
            volatility=15.0,
            path_dispersion=3.2,
            confidence_score=80.0,
            sample_count_used=100,
            p10=-5.0,
            p25=-2.0,
            p50=2.5,
            p75=6.0,
            p90=10.0,
        )
        assert d.expected_return == 2.5
        assert d.p50 == 2.5
        assert d.p10 == -5.0

    def test_to_dict_roundtrip(self) -> None:
        original = PredictionDistribution(
            expected_return=3.0,
            direction=Direction.UP,
            volatility=20.0,
            confidence_score=85.0,
            p10=-6.0,
            p50=3.0,
            p90=12.0,
        )
        restored = PredictionDistribution.from_dict(original.to_dict())
        assert restored.expected_return == original.expected_return
        assert restored.direction == original.direction
        assert restored.p50 == original.p50

    def test_empty_classmethod(self) -> None:
        d = PredictionDistribution.empty()
        assert d.expected_return is None
        assert d.direction is None


# ── TAAnalysis ────────────────────────────────────────────────────────────────


class TestTAAnalysis:
    def test_minimal(self) -> None:
        a = TAAnalysis(ticker="sh.600519", eval_date="2026-10-09", signal=Signal.BUY, confidence=80.0)
        assert a.ticker == "sh.600519"
        assert a.error is None

    def test_to_dict_contains_all_fields(self) -> None:
        a = TAAnalysis(
            ticker="sh.600519",
            eval_date="2026-10-09",
            signal=Signal.BUY,
            confidence=80.0,
            thesis="基本面良好",
            risks=["宏观风险"],
            valuation_score=75.0,
            catalysts=["年报超预期"],
        )
        d = a.to_dict()
        assert d["ticker"] == "sh.600519"
        assert d["signal"] == Signal.BUY
        assert d["risks"] == ["宏观风险"]
        assert d["catalysts"] == ["年报超预期"]

    def test_from_dict_with_signal_string(self) -> None:
        """从 dict 构造时，字符串信号正确解析为 Signal 枚举。"""
        data = {"ticker": "sh.600519", "eval_date": "2026-10-09", "signal": "BUY", "confidence": 75.0}
        a = TAAnalysis.from_dict(data)
        assert a.signal == Signal.BUY

    def test_from_dict_invalid_signal_fallback_to_hold(self) -> None:
        """无效信号字符串 → 回退 HOLD。"""
        data = {"ticker": "sh.600519", "eval_date": "2026-10-09", "signal": "STRONG_BUY", "confidence": 75.0}
        a = TAAnalysis.from_dict(data)
        assert a.signal == Signal.HOLD

    def test_from_dict_missing_signal_defaults_to_hold(self) -> None:
        """缺少 signal 字段 → 默认 HOLD。"""
        data = {"ticker": "sh.600519", "eval_date": "2026-10-09", "confidence": 75.0}
        a = TAAnalysis.from_dict(data)
        assert a.signal == Signal.HOLD

    def test_failed_classmethod(self) -> None:
        a = TAAnalysis.failed("sh.600519", "2026-10-09", "API timeout")
        assert a.signal == Signal.HOLD
        assert a.confidence == 0.0
        assert a.error == "API timeout"
        assert a.ticker == "sh.600519"

    def test_all_score_fields(self) -> None:
        """所有评分字段均可填充。"""
        a = TAAnalysis(
            ticker="sh.600519",
            eval_date="2026-10-09",
            signal=Signal.BUY,
            confidence=80.0,
            valuation_score=70.0,
            fundamental_score=85.0,
            technical_score=60.0,
            sentiment_score=75.0,
            capital_flow_score=80.0,
            macro_score=65.0,
        )
        assert a.valuation_score == 70.0
        assert a.fundamental_score == 85.0
        assert a.technical_score == 60.0


# ── KronosPrediction ──────────────────────────────────────────────────────────


class TestKronosPrediction:
    def test_minimal(self) -> None:
        dist = PredictionDistribution(expected_return=2.0, direction=Direction.UP, p50=2.0)
        p = KronosPrediction(
            ticker="sh.600519",
            eval_date="2026-10-09",
            horizon=5,
            direction=Direction.UP,
            expected_return=2.5,
            predicted_close=1850.0,
            distribution=dist,
        )
        assert p.ticker == "sh.600519"
        assert p.horizon == 5

    def test_property_delegates_to_distribution(self) -> None:
        dist = PredictionDistribution(p10=-5.0, p25=-2.0, p50=2.5, p75=6.0, p90=10.0)
        p = KronosPrediction(
            ticker="sh.600519",
            eval_date="2026-10-09",
            horizon=5,
            direction=Direction.UP,
            expected_return=2.5,
            predicted_close=1850.0,
            distribution=dist,
        )
        assert p.p10 == -5.0
        assert p.p50 == 2.5
        assert p.p90 == 10.0

    def test_to_dict(self) -> None:
        dist = PredictionDistribution(direction=Direction.UP, p50=2.5)
        p = KronosPrediction(
            ticker="sh.600519", eval_date="2026-10-09", horizon=5,
            direction=Direction.UP, expected_return=2.5,
            predicted_close=1850.0, distribution=dist,
        )
        d = p.to_dict()
        assert d["ticker"] == "sh.600519"
        assert d["direction"] == "UP"
        assert d["expected_return"] == 2.5

    def test_from_dict(self) -> None:
        data = {
            "ticker": "sh.600519",
            "eval_date": "2026-10-09",
            "horizon": 5,
            "direction": "UP",
            "expected_return": 2.5,
            "predicted_close": 1850.0,
            "distribution": {"direction": "UP", "p50": 2.5},
            "model_name": "kronos-base",
        }
        p = KronosPrediction.from_dict(data)
        assert p.ticker == "sh.600519"
        assert p.direction == Direction.UP
        assert p.model_name == "kronos-base"

    def test_failed_classmethod(self) -> None:
        p = KronosPrediction.failed("sh.600519", "2026-10-09", horizon=5, error="torch not found")
        assert p.error == "torch not found"
        assert p.direction == Direction.FLAT


# ── EvalRecord 测试（来自 domain.evaluation）────────────────────────────────


class TestEvalRecord:
    def test_defaults(self) -> None:
        from trade_krono_cli.eval_data import EvalRecord as ER
        r = ER(ticker="sh.600519", eval_date="2026-10-09", horizon_days=5,
               pred_direction="UP", pred_return_pct=1.0, actual_return_pct=1.0,
               actual_direction="UP", is_direction_correct=True, error_pct=0.0)
        assert r.ticker == "sh.600519"
        assert r.horizon_days == 5

    def test_full_initialization(self) -> None:
        from trade_krono_cli.eval_data import EvalRecord as ER
        r = ER(
            ticker="sz.000858",
            eval_date="2026-09-01",
            horizon_days=10,
            pred_direction="UP",
            pred_return_pct=3.0,
            actual_return_pct=2.5,
            actual_direction="UP",
            is_direction_correct=True,
            error_pct=0.5,
        )
        assert r.ticker == "sz.000858"
        assert r.actual_return_pct == 2.5

    def test_to_dict_roundtrip(self) -> None:
        from trade_krono_cli.domain.evaluation import EvalRecord as ER
        original = ER(
            ticker="sh.601318",
            eval_date="2026-08-15",
            horizon_days=5,
            pred_direction="UP",
            pred_return_pct=2.0,
            actual_return_pct=-0.5,
            actual_direction="DOWN",
            is_direction_correct=False,
            error_pct=2.5,
        )
        restored = ER.from_dict(original.to_dict())
        assert restored.ticker == original.ticker
        assert restored.actual_return_pct == original.actual_return_pct
