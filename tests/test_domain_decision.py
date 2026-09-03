"""测试领域决策对象 InvestmentDecision。"""

from __future__ import annotations

import pytest

from trade_krono_cli.domain.decision import InvestmentDecision
from trade_krono_cli.domain.signal import SignalAssessment
from trade_krono_cli.domain.types import Signal

# ── 基本构造 ──────────────────────────────────────────────────────────────────


def test_basic() -> None:
    d = InvestmentDecision(
        ticker="sh.600519",
        eval_date="2026-08-11",
        signal=Signal.BUY,
        confidence=85.0,
        thesis="核心论点",
        risks=["风险A", "风险B"],
    )
    assert d.ticker == "sh.600519"
    assert d.signal == Signal.BUY
    assert d.confidence == 85.0
    assert d.thesis == "核心论点"
    assert d.risks == ["风险A", "风险B"]


def test_frozen() -> None:
    d = InvestmentDecision(
        ticker="sh.600519",
        eval_date="2026-08-11",
        signal=Signal.BUY,
        confidence=80.0,
    )
    with pytest.raises(AttributeError):
        d.ticker = "sz.000858"  # type: ignore[misc]


def test_defaults() -> None:
    d = InvestmentDecision(
        ticker="sh.600519",
        eval_date="2026-08-11",
        signal=Signal.HOLD,
        confidence=50.0,
    )
    assert d.expected_value is None
    assert d.prob_win is None
    assert d.risk_adjusted_ev is None
    assert d.entry_zone is None
    assert d.target_price is None
    assert d.stop_loss is None
    assert d.horizon is None


# ── EV 指标传播 ───────────────────────────────────────────────────────────────


def test_with_ev_fields() -> None:
    d = InvestmentDecision(
        ticker="sh.600519",
        eval_date="2026-08-11",
        signal=Signal.BUY,
        confidence=80.0,
        expected_value=5.5,
        prob_win=0.72,
        risk_adjusted_ev=1.23,
    )
    assert d.expected_value == 5.5
    assert d.prob_win == 0.72
    assert d.risk_adjusted_ev == 1.23


# ── to_dict / from_dict ───────────────────────────────────────────────────────


def test_to_dict() -> None:
    d = InvestmentDecision(
        ticker="sh.600519",
        eval_date="2026-08-11",
        signal=Signal.BUY,
        confidence=80.0,
        thesis="论点",
        risks=["风险"],
        entry_zone=[148.0, 152.0],
        target_price=170.0,
        stop_loss=140.0,
        expected_value=5.5,
        prob_win=0.72,
    )
    result = d.to_dict()
    assert result["ticker"] == "sh.600519"
    assert result["signal"] == "BUY"
    assert result["thesis"] == "论点"
    assert result["entry_zone"] == [148.0, 152.0]
    assert result["target_price"] == 170.0
    assert result["stop_loss"] == 140.0
    assert result["expected_value"] == 5.5
    assert result["prob_win"] == 0.72
    assert result["ranking_score"] is None


def test_to_dict_no_optional_fields() -> None:
    d = InvestmentDecision(
        ticker="sh.600519",
        eval_date="2026-08-11",
        signal=Signal.HOLD,
        confidence=50.0,
    )
    result = d.to_dict()
    # to_dict always includes all fields (even None ones); verify key structure
    assert result["ticker"] == "sh.600519"
    assert result["signal"] == "HOLD"
    assert result["confidence"] == 50.0
    # optional fields are present but None
    assert result["target_price"] is None
    assert result["stop_loss"] is None
    assert result["entry_zone"] is None
    assert result["expected_value"] is None
    assert result["prob_win"] is None


def test_from_dict() -> None:
    data = {
        "ticker": "sh.600519",
        "eval_date": "2026-08-11",
        "signal": "BUY",
        "confidence": 80.0,
        "thesis": "论点",
        "risks": ["风险"],
        "entry_zone": [148.0, 152.0],
        "target_price": 170.0,
        "stop_loss": 140.0,
        "expected_value": 5.5,
        "prob_win": 0.72,
    }
    d = InvestmentDecision.from_dict(data)
    assert d.ticker == "sh.600519"
    assert d.signal == Signal.BUY
    assert d.target_price == 170.0
    assert d.stop_loss == 140.0


def test_from_dict_missing_optional() -> None:
    data = {"ticker": "sh.600519", "eval_date": "2026-08-11", "signal": "HOLD", "confidence": 50.0}
    d = InvestmentDecision.from_dict(data)
    assert d.target_price is None
    assert d.stop_loss is None


def test_from_dict_invalid_signal() -> None:
    data = {
        "ticker": "sh.600519",
        "eval_date": "2026-08-11",
        "signal": "UNKNOWN",
        "confidence": 50.0,
    }
    # Invalid signal falls back to HOLD (no exception)
    d = InvestmentDecision.from_dict(data)
    assert d.signal == Signal.HOLD


def test_from_dict_invalid_confidence() -> None:
    data = {
        "ticker": "sh.600519",
        "eval_date": "2026-08-11",
        "signal": "BUY",
        "confidence": "not_a_number",
    }
    # confidence is cast with float() — invalid string raises ValueError
    with pytest.raises(ValueError):
        InvestmentDecision.from_dict(data)


def test_from_dict_empty() -> None:
    with pytest.raises(KeyError):
        InvestmentDecision.from_dict({})


# ── ranking_score / composite_score 向后兼容 ─────────────────────────────────


def test_to_dict_ranking_score() -> None:
    d = InvestmentDecision(
        ticker="sh.600519",
        eval_date="2026-08-11",
        signal=Signal.BUY,
        confidence=80.0,
        ranking_score=75.0,
    )
    result = d.to_dict()
    assert result["ranking_score"] == 75.0


def test_from_dict_composite_score_backward_compat() -> None:
    """composite_score 字段向后兼容到 ranking_score。"""
    data = {
        "ticker": "sh.600519",
        "eval_date": "2026-08-11",
        "signal": "BUY",
        "confidence": 80.0,
        "composite_score": 75.0,
    }
    d = InvestmentDecision.from_dict(data)
    assert d.ranking_score == 75.0


# ── empty decision ────────────────────────────────────────────────────────────


def test_empty() -> None:
    """InvestmentDecision 无 empty() 工厂方法；使用 fallback 或最小构造。"""
    d = InvestmentDecision(
        ticker="",
        eval_date="",
        signal=Signal.HOLD,
        confidence=0.0,
    )
    assert d.signal == Signal.HOLD
    assert d.confidence == 0.0
    assert d.ticker == ""


# ── 领域工厂测试（domain/factory.py build_investment_decision）────────────────


def test_domain_factory_build_decision() -> None:
    from trade_krono_cli.domain.factory import build_investment_decision

    assessment = SignalAssessment(
        ticker="sh.600519",
        eval_date="2026-08-11",
        final_signal=Signal.BUY,
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
    assert d.signal == Signal.BUY
    assert d.expected_value == 5.5
    assert d.prob_win == 0.72
