"""eval_ta / eval_kronos 补充边界测试。

覆盖 test_eval_and_reporter.py 未触及的分支：
- ta_buy 混合胜负（win_rate 非 100/0）、HOLD 平均收益
- pred_direction 为 None 的记录不计入 Kronos 准确率
- 全 HOLD 记录（无 BUY）时 metrics 保持不变
"""

from __future__ import annotations

import pytest

from trade_krono_cli.eval_data import EvalRecord, HorizonMetrics
from trade_krono_cli.eval_kronos import compute_kronos_accuracy
from trade_krono_cli.eval_ta import compute_ta_metrics


def _record(
    ta_signal: str | None = None,
    pred_direction: str | None = "UP",
    actual_return_pct: float = 0.0,
    is_correct: bool = True,
) -> EvalRecord:
    return EvalRecord(
        ticker="sh.600519",
        eval_date="2026-09-01",
        horizon_days=5,
        pred_direction=pred_direction,
        pred_return_pct=1.0,
        actual_return_pct=actual_return_pct,
        actual_direction="UP",
        is_direction_correct=is_correct,
        error_pct=0.5,
        ta_signal=ta_signal,
    )


class TestComputeTaMetricsBranches:
    def test_mixed_wins_and_losses_win_rate_50(self) -> None:
        records = [
            _record(ta_signal="BUY", actual_return_pct=2.0),
            _record(ta_signal="BUY", actual_return_pct=-1.0),
        ]
        metrics = HorizonMetrics()
        ta_buy_n, ta_hold_n = compute_ta_metrics(records, metrics)
        assert ta_buy_n == 2
        assert ta_hold_n == 0
        assert metrics.ta_buy_win_rate == pytest.approx(50.0)
        assert metrics.ta_buy_avg_return == pytest.approx(0.5)

    def test_hold_only_updates_hold_avg_return(self) -> None:
        records = [
            _record(ta_signal="HOLD", actual_return_pct=1.0),
            _record(ta_signal="HOLD", actual_return_pct=3.0),
        ]
        metrics = HorizonMetrics()
        ta_buy_n, ta_hold_n = compute_ta_metrics(records, metrics)
        assert ta_buy_n == 0
        assert ta_hold_n == 2
        # 无 BUY 记录时 win_rate 保持默认 0
        assert metrics.ta_buy_win_rate == 0.0
        assert metrics.ta_hold_avg_return == pytest.approx(2.0)

    def test_ta_signal_none_ignored(self) -> None:
        records = [_record(ta_signal=None, actual_return_pct=5.0)]
        metrics = HorizonMetrics()
        ta_buy_n, ta_hold_n = compute_ta_metrics(records, metrics)
        assert ta_buy_n == 0
        assert ta_hold_n == 0
        assert metrics.ta_hold_avg_return == 0.0


class TestComputeKronosAccuracyBranches:
    def test_records_with_none_pred_direction_excluded(self) -> None:
        records = [
            _record(pred_direction=None, actual_return_pct=5.0, is_correct=False),
            _record(pred_direction="UP", actual_return_pct=2.0, is_correct=True),
            _record(pred_direction="DOWN", actual_return_pct=-2.0, is_correct=True),
        ]
        metrics = HorizonMetrics()
        n = compute_kronos_accuracy(records, metrics)
        # 只有 2 条有 pred_direction
        assert n == 2
        assert metrics.kronos_dir_accuracy == pytest.approx(100.0)

    def test_all_none_pred_direction_returns_zero(self) -> None:
        records = [_record(pred_direction=None)]
        metrics = HorizonMetrics()
        n = compute_kronos_accuracy(records, metrics)
        assert n == 0
        assert metrics.kronos_dir_accuracy == 0.0


class TestHorizonMetricsDefaults:
    def test_new_metrics_all_zero(self) -> None:
        m = HorizonMetrics()
        assert m.ta_buy_win_rate == 0.0
        assert m.ta_buy_avg_return == 0.0
        assert m.ta_hold_avg_return == 0.0
        assert m.kronos_dir_accuracy == 0.0
        assert m.profit_factor == 0.0
        assert m.sharpe_ratio == 0.0
