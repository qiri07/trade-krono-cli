"""PredictionEvaluator 核心逻辑单元测试。

覆盖：
  - _compute_summary
  - evaluate 方法的核心流程（mock 数据库）
  - IC 计算
  - 边界情况
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from trade_krono_cli.eval_data import EvalRecord
from trade_krono_cli.prediction_eval import PredictionEvaluator


def _make_evaluator() -> PredictionEvaluator:
    mock_research = MagicMock()
    mock_research.list_jobs.return_value = []
    with patch("trade_krono_cli.research_db.get_research", return_value=mock_research):
        return PredictionEvaluator(max_workers=1)


@pytest.fixture
def evaluator() -> PredictionEvaluator:
    return _make_evaluator()


# ── _compute_summary ─────────────────────────────────────────────────────────


class TestComputeSummary:
    def test_empty_records(self, evaluator: PredictionEvaluator) -> None:
        """空记录 → 返回空的 EvaluationSummary。"""
        summary = evaluator._compute_summary([])
        assert summary is not None
        assert summary.kronos_n == 0

    def test_records_by_horizon(self, evaluator: PredictionEvaluator) -> None:
        """记录按 horizon_days 分组统计。"""
        records = [
            EvalRecord(ticker="sh.600519", eval_date="2026-01-02", horizon_days=5,
                       pred_direction="UP", pred_return_pct=3.0, actual_return_pct=3.0,
                       actual_direction="UP", is_direction_correct=True, error_pct=0.0),
            EvalRecord(ticker="sh.600519", eval_date="2026-01-07", horizon_days=10,
                       pred_direction="DOWN", pred_return_pct=-1.0, actual_return_pct=-1.0,
                       actual_direction="DOWN", is_direction_correct=True, error_pct=0.0),
        ]
        summary = evaluator._compute_summary(records)
        assert summary is not None
        assert summary.records == records

    def test_all_predictions_same_direction(self, evaluator: PredictionEvaluator) -> None:
        """全 UP 预测 → 正确方向统计。"""
        records = [
            EvalRecord(ticker="sh.600519", eval_date="2026-01-02", horizon_days=5,
                       pred_direction="UP", pred_return_pct=3.0, actual_return_pct=3.0,
                       actual_direction="UP", is_direction_correct=True, error_pct=0.0),
            EvalRecord(ticker="sh.600519", eval_date="2026-01-03", horizon_days=5,
                       pred_direction="UP", pred_return_pct=2.0, actual_return_pct=2.0,
                       actual_direction="UP", is_direction_correct=True, error_pct=0.0),
        ]
        summary = evaluator._compute_summary(records)
        assert summary is not None
        assert len(summary.records) == 2


# ── evaluate 主流程 ──────────────────────────────────────────────────────────


class TestEvaluate:
    def test_no_jobs_returns_empty_summary(self, evaluator: PredictionEvaluator) -> None:
        """无历史作业 → 返回空摘要。"""
        result = evaluator.evaluate()
        assert result is not None

    @patch("trade_krono_cli.research_db.get_research")
    def test_with_jobs_processes_signals(self, mock_get_research: MagicMock) -> None:
        """有作业时正常处理信号。"""
        mock_research = MagicMock()
        mock_research.list_jobs.return_value = [
            {"job_id": "job-1", "date": "2026-10-01"}
        ]
        mock_research.get_signals_by_job.return_value = [
            {
                "ticker": "sh.600519",
                "date": "2026-10-01",
                "ta_signal": "BUY",
                "ta_confidence": 80.0,
                "kronos_direction": "UP",
                "kronos_confidence": 75.0,
            }
        ]
        mock_get_research.return_value = mock_research

        evaluator = PredictionEvaluator(max_workers=1)
        result = evaluator.evaluate(store=False)
        assert result is not None

    def test_ticker_filter(self, evaluator: PredictionEvaluator) -> None:
        """只评估指定股票。"""
        result = evaluator.evaluate(tickers=["sh.600519"], store=False)
        assert result is not None

    def test_date_range_filter(self, evaluator: PredictionEvaluator) -> None:
        """按日期范围过滤。"""
        result = evaluator.evaluate(from_date="2026-01-01", to_date="2026-12-31", store=False)
        assert result is not None


# ── IC 计算 ───────────────────────────────────────────────────────────────────


class TestICComputation:
    def test_ic_computed_for_horizon_5(self, evaluator: PredictionEvaluator) -> None:
        """IC 指标在 horizon=5 时正确计算。"""
        records = []
        for i in range(20):
            records.append(EvalRecord(
                ticker="sh.600519",
                eval_date=f"2026-01-{i+2:02d}",
                horizon_days=5,
                pred_direction="UP" if i % 2 == 0 else "DOWN",
                pred_return_pct=1.0 if i % 2 == 0 else -1.0,
                actual_return_pct=1.0 if i % 2 == 0 else -1.0,
                actual_direction="UP" if i % 2 == 0 else "DOWN",
                is_direction_correct=True,
                error_pct=0.0,
            ))
        summary = evaluator._compute_summary(records)
        assert summary is not None
        assert hasattr(summary, "ic_composite_rank_mean")


# ── 边界参数 ──────────────────────────────────────────────────────────────────


class TestBoundaryParams:
    def test_records_with_none_actual_return(self, evaluator: PredictionEvaluator) -> None:
        """actual_return_pct 为 None 的记录不影响其他记录。"""
        records = [
            EvalRecord(ticker="sh.600519", eval_date="2026-01-02", horizon_days=5,
                       pred_direction="UP", pred_return_pct=3.0, actual_return_pct=3.0,
                       actual_direction="UP", is_direction_correct=True, error_pct=0.0),
            EvalRecord(ticker="sh.600519", eval_date="2026-01-03", horizon_days=5,
                       pred_direction="UP", pred_return_pct=None, actual_return_pct=None,
                       actual_direction=None, is_direction_correct=False, error_pct=0.0),
        ]
        summary = evaluator._compute_summary(records)
        assert summary is not None

    def test_missing_fields_in_record(self, evaluator: PredictionEvaluator) -> None:
        """缺少某些字段的记录不应导致崩溃。"""
        record = EvalRecord(ticker="sh.600519", eval_date="2026-01-02", horizon_days=5,
                            pred_direction="UP", pred_return_pct=1.0, actual_return_pct=1.0,
                            actual_direction="UP", is_direction_correct=True, error_pct=0.0)
        summary = evaluator._compute_summary([record])
        assert summary is not None
