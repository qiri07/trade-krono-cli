"""测试 domain/evaluation.py — EvalRecord / EvaluationSummary / HorizonMetrics。"""

from __future__ import annotations

import pytest

from trade_krono_cli.domain.evaluation import (
    EvalRecord,
    EvaluationSummary,
    HorizonMetrics,
)
from trade_krono_cli.domain.types import Direction

# ── EvalRecord ────────────────────────────────────────────────────────────────


def test_basic() -> None:
    record = EvalRecord(
        ticker="sh.600519",
        eval_date="2026-08-11",
        horizon_days=5,
        pred_direction=Direction.UP,
        pred_return_pct=3.2,
        actual_return_pct=2.5,
        actual_direction="UP",
        is_direction_correct=True,
        error_pct=0.7,
        ta_signal="BUY",
    )
    assert record.ticker == "sh.600519"
    assert record.pred_direction == Direction.UP
    assert record.actual_direction == "UP"
    assert record.is_direction_correct is True
    assert record.error_pct == pytest.approx(0.7, abs=0.01)


def test_frozen() -> None:
    record = EvalRecord(
        ticker="sh.600519",
        eval_date="2026-08-11",
        horizon_days=5,
        pred_direction=Direction.UP,
        pred_return_pct=3.2,
        actual_return_pct=2.5,
        actual_direction="UP",
        is_direction_correct=True,
    )
    with pytest.raises(AttributeError):
        record.ticker = "sz.000858"  # type: ignore[misc]


def test_to_dict() -> None:
    record = EvalRecord(
        ticker="sh.600519",
        eval_date="2026-08-11",
        horizon_days=5,
        pred_direction=Direction.UP,
        pred_return_pct=3.2,
        actual_return_pct=2.5,
        actual_direction="UP",
        is_direction_correct=True,
        ta_signal="BUY",
        ranking_score=75.0,
        expected_value=5.0,
    )
    d = record.to_dict()
    assert d["ticker"] == "sh.600519"
    assert d["pred_direction"] == "UP"
    assert d["actual_direction"] == "UP"
    assert d["ta_signal"] == "BUY"
    assert d["ranking_score"] == 75.0
    assert d["expected_value"] == 5.0


def test_from_dict() -> None:
    data = {
        "ticker": "sh.600519",
        "eval_date": "2026-08-11",
        "horizon_days": 5,
        "pred_direction": "UP",
        "pred_return_pct": 3.2,
        "actual_return_pct": 2.5,
        "actual_direction": "UP",
        "is_direction_correct": True,
        "ta_signal": "BUY",
    }
    record = EvalRecord.from_dict(data)
    assert record.ticker == "sh.600519"
    assert record.pred_direction == Direction.UP


def test_from_dict_invalid_direction() -> None:
    """pred_direction 为非法字符串时，EvalRecord 接受原始值（不做枚举校验）。"""
    data = {
        "ticker": "sh.600519",
        "eval_date": "2026-08-11",
        "horizon_days": 5,
        "pred_direction": "UNKNOWN",
        "pred_return_pct": 3.2,
        "actual_return_pct": 2.5,
        "actual_direction": "UP",
        "is_direction_correct": True,
    }
    record = EvalRecord.from_dict(data)
    assert record.pred_direction == "UNKNOWN"


def test_from_dict_missing_pred_direction() -> None:
    """pred_direction 为 None 时不校验 Direction 枚举。"""
    data = {
        "ticker": "sh.600519",
        "eval_date": "2026-08-11",
        "horizon_days": 5,
        "pred_direction": None,
        "pred_return_pct": None,
        "actual_return_pct": 2.5,
        "actual_direction": "UP",
        "is_direction_correct": False,
    }
    record = EvalRecord.from_dict(data)
    assert record.pred_direction is None
    assert record.is_direction_correct is False


# ── HorizonMetrics ────────────────────────────────────────────────────────────


def test_defaults() -> None:
    m = HorizonMetrics()
    assert m.kronos_dir_accuracy == 0.0
    assert m.ta_buy_win_rate == 0.0
    assert m.combined_buy_up_win_rate == 0.0
    assert m.high_conf_win_rate == 0.0


def test_with_values() -> None:
    m = HorizonMetrics(
        kronos_dir_accuracy=72.5,
        ta_buy_win_rate=65.0,
        combined_buy_up_win_rate=70.0,
        high_conf_win_rate=80.0,
    )
    assert m.kronos_dir_accuracy == 72.5


def test_horizon_metrics_to_dict() -> None:
    m = HorizonMetrics(
        kronos_dir_accuracy=75.0,
        ta_buy_win_rate=60.0,
        combined_buy_up_win_rate=65.0,
        high_conf_win_rate=70.0,
    )
    d = m.to_dict()
    assert d["kronos_dir_accuracy"] == 75.0


# ── EvaluationSummary ─────────────────────────────────────────────────────────


def test_empty_summary() -> None:
    s = EvaluationSummary()
    assert s.kronos_n == 0
    assert s.ta_buy_n == 0
    assert s.combined_buy_up_n == 0
    assert s.high_conf_n == 0


def test_summary_with_horizons() -> None:
    m5 = HorizonMetrics(kronos_dir_accuracy=70.0)
    m10 = HorizonMetrics(kronos_dir_accuracy=65.0)
    s = EvaluationSummary(horizons={5: m5, 10: m10})
    assert s.horizons[5].kronos_dir_accuracy == 70.0
    assert s.horizons[10].kronos_dir_accuracy == 65.0


def test_summary_to_dict() -> None:
    m5 = HorizonMetrics(kronos_dir_accuracy=70.0)
    s = EvaluationSummary(horizons={5: m5})
    d = s.to_dict()
    assert "horizons" in d
    assert d["horizons"]["5"]["kronos_dir_accuracy"] == 70.0


def test_summary_from_dict() -> None:
    _data = {
        "horizons": {
            "5": {
                "kronos_dir_accuracy": 70.0,
                "ta_buy_win_rate": 65.0,
                "combined_buy_up_win_rate": 68.0,
                "high_conf_win_rate": 75.0,
            },
        },
    }
    s = EvaluationSummary(
        horizons={
            5: HorizonMetrics(
                kronos_dir_accuracy=70.0,
                ta_buy_win_rate=65.0,
                combined_buy_up_win_rate=68.0,
                high_conf_win_rate=75.0,
            ),
        },
    )
    assert s.horizons[5].kronos_dir_accuracy == 70.0


def test_summary_roundtrip() -> None:
    m5 = HorizonMetrics(
        kronos_dir_accuracy=70.0,
        ta_buy_win_rate=65.0,
        combined_buy_up_win_rate=68.0,
        high_conf_win_rate=75.0,
    )
    s = EvaluationSummary(horizons={5: m5})
    d = s.to_dict()
    s2 = EvaluationSummary(horizons={5: HorizonMetrics(**d["horizons"]["5"])})
    assert s2.horizons[5].kronos_dir_accuracy == 70.0
