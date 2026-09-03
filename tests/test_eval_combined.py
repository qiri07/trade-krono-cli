"""测试 eval_combined.py — 综合信号与高置信度评估。"""

from __future__ import annotations

import pytest

from trade_krono_cli.eval_combined import (
    compute_combined_metrics,
    compute_high_conf_metrics,
)
from trade_krono_cli.eval_data import EvalRecord, HorizonMetrics

# ── 辅助函数 ──────────────────────────────────────────────────────────────────


def _make_record(
    ta_signal: str = "BUY",
    pred_direction: str = "UP",
    actual_return_pct: float = 2.5,
    composite_score: float | None = 75.0,
) -> EvalRecord:
    """创建简化 EvalRecord。"""
    return EvalRecord(
        ticker="sh.600519",
        eval_date="2026-08-12",
        horizon_days=3,
        pred_direction=pred_direction,
        pred_return_pct=2.0,
        actual_return_pct=actual_return_pct,
        actual_direction="UP" if actual_return_pct > 0 else "DOWN",
        is_direction_correct=True,
        error_pct=0.5,
        ta_signal=ta_signal,
        composite_score=composite_score,
    )


# ── compute_combined_metrics ──────────────────────────────────────────────────


class TestComputeCombinedMetrics:
    """综合信号（TA BUY + Kronos UP）指标计算测试。"""

    def test_empty_records(self) -> None:
        """空记录应返回 0，不修改 metrics。"""
        metrics = HorizonMetrics()
        result = compute_combined_metrics([], metrics)
        assert result == 0

    def test_no_combined_signal(self) -> None:
        """没有 TA BUY + Kronos UP 组合时应返回 0。"""
        records = [
            _make_record(ta_signal="SELL", pred_direction="UP"),
            _make_record(ta_signal="BUY", pred_direction="DOWN"),
        ]
        metrics = HorizonMetrics()
        result = compute_combined_metrics(records, metrics)
        assert result == 0
        assert metrics.combined_buy_up_win_rate == 0.0
        assert metrics.combined_buy_up_avg_return == 0.0

    def test_all_combined_wins(self) -> None:
        """所有组合信号都盈利 → win_rate=100。"""
        records = [
            _make_record(ta_signal="BUY", pred_direction="UP", actual_return_pct=3.0),
            _make_record(ta_signal="BUY", pred_direction="UP", actual_return_pct=5.0),
            _make_record(ta_signal="BUY", pred_direction="UP", actual_return_pct=1.0),
        ]
        metrics = HorizonMetrics()
        result = compute_combined_metrics(records, metrics)
        assert result == 3
        assert metrics.combined_buy_up_win_rate == 100.0
        assert metrics.combined_buy_up_avg_return == pytest.approx(3.0)

    def test_mixed_outcomes(self) -> None:
        """混合结果：2胜1负 → win_rate≈66.7。"""
        records = [
            _make_record(ta_signal="BUY", pred_direction="UP", actual_return_pct=2.0),
            _make_record(ta_signal="BUY", pred_direction="UP", actual_return_pct=4.0),
            _make_record(ta_signal="BUY", pred_direction="UP", actual_return_pct=-1.0),
        ]
        metrics = HorizonMetrics()
        result = compute_combined_metrics(records, metrics)
        assert result == 3
        assert metrics.combined_buy_up_win_rate == pytest.approx(66.7)
        assert metrics.combined_buy_up_avg_return == pytest.approx(1.67)

    def test_all_combined_losses(self) -> None:
        """所有组合信号都亏损 → win_rate=0。"""
        records = [
            _make_record(ta_signal="BUY", pred_direction="UP", actual_return_pct=-2.0),
            _make_record(ta_signal="BUY", pred_direction="UP", actual_return_pct=-5.0),
        ]
        metrics = HorizonMetrics()
        result = compute_combined_metrics(records, metrics)
        assert result == 2
        assert metrics.combined_buy_up_win_rate == 0.0
        assert metrics.combined_buy_up_avg_return == pytest.approx(-3.5)

    def test_with_non_combined_records_ignored(self) -> None:
        """非组合信号应被忽略。"""
        records = [
            _make_record(ta_signal="BUY", pred_direction="UP", actual_return_pct=5.0),
            _make_record(ta_signal="SELL", pred_direction="DOWN", actual_return_pct=10.0),  # 不参与
            _make_record(ta_signal="HOLD", pred_direction="UP", actual_return_pct=3.0),  # 不参与
        ]
        metrics = HorizonMetrics()
        result = compute_combined_metrics(records, metrics)
        assert result == 1
        assert metrics.combined_buy_up_win_rate == 100.0


# ── compute_high_conf_metrics ────────────────────────────────────────────────


class TestComputeHighConfMetrics:
    """高置信信号（composite_score ≥ 70）指标计算测试。"""

    def test_empty_records(self) -> None:
        """空记录应返回 0。"""
        metrics = HorizonMetrics()
        result = compute_high_conf_metrics([], metrics)
        assert result == 0

    def test_no_high_conf_signal(self) -> None:
        """没有高置信信号时应返回 0。"""
        records = [
            _make_record(composite_score=50.0),
            _make_record(composite_score=None),
        ]
        metrics = HorizonMetrics()
        result = compute_high_conf_metrics(records, metrics)
        assert result == 0
        assert metrics.high_conf_win_rate == 0.0
        assert metrics.high_conf_avg_return == 0.0

    def test_all_high_conf_wins(self) -> None:
        """所有高置信信号都盈利。"""
        records = [
            _make_record(composite_score=80.0, actual_return_pct=4.0),
            _make_record(composite_score=75.0, actual_return_pct=2.0),
            _make_record(composite_score=90.0, actual_return_pct=6.0),
        ]
        metrics = HorizonMetrics()
        result = compute_high_conf_metrics(records, metrics)
        assert result == 3
        assert metrics.high_conf_win_rate == 100.0
        assert metrics.high_conf_avg_return == pytest.approx(4.0)

    def test_mixed_outcomes(self) -> None:
        """混合结果。"""
        records = [
            _make_record(composite_score=85.0, actual_return_pct=5.0),
            _make_record(composite_score=72.0, actual_return_pct=-2.0),
            _make_record(composite_score=78.0, actual_return_pct=3.0),
        ]
        metrics = HorizonMetrics()
        result = compute_high_conf_metrics(records, metrics)
        assert result == 3
        assert metrics.high_conf_win_rate == pytest.approx(66.7)
        assert metrics.high_conf_avg_return == pytest.approx(2.0)

    def test_boundary_score(self) -> None:
        """composite_score=70 应包含（边界值）。"""
        records = [
            _make_record(composite_score=70.0, actual_return_pct=2.0),  # 刚好边界
            _make_record(composite_score=69.9, actual_return_pct=10.0),  # 不包含
        ]
        metrics = HorizonMetrics()
        result = compute_high_conf_metrics(records, metrics)
        assert result == 1
        assert metrics.high_conf_avg_return == pytest.approx(2.0)

    def test_none_score_ignored(self) -> None:
        """composite_score=None 应被忽略。"""
        records = [
            _make_record(composite_score=80.0, actual_return_pct=3.0),
            _make_record(composite_score=None, actual_return_pct=10.0),
        ]
        metrics = HorizonMetrics()
        result = compute_high_conf_metrics(records, metrics)
        assert result == 1
        assert metrics.high_conf_win_rate == 100.0

    def test_with_non_high_conf_records_ignored(self) -> None:
        """非高置信信号应被忽略。"""
        records = [
            _make_record(composite_score=85.0, actual_return_pct=5.0),
            _make_record(composite_score=60.0, actual_return_pct=20.0),  # 不包含
            _make_record(composite_score=90.0, actual_return_pct=8.0),
        ]
        metrics = HorizonMetrics()
        result = compute_high_conf_metrics(records, metrics)
        assert result == 2
        assert metrics.high_conf_avg_return == pytest.approx(6.5)
