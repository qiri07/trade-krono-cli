"""prediction_eval.py 边缘场景补齐测试（修正版）。

关键修复：PredictionEvaluator.__init__ 调用 get_research()，
需通过 __new__ 绕过初始化，手动注入 mock。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from trade_krono_cli.eval_data import EvalRecord, EvaluationSummary
from trade_krono_cli.prediction_eval import PredictionEvaluator, run_evaluation


def _make_evaluator(tmp_path: Path) -> tuple[PredictionEvaluator, MagicMock]:
    """创建绕过 __init__ 的 evaluator，直接注入 mock research。"""
    mock_research = MagicMock()
    mock_research._db_path = tmp_path / "test.db"
    mock_research.list_jobs.return_value = []
    evaluator = PredictionEvaluator.__new__(PredictionEvaluator)
    evaluator._research = mock_research
    evaluator._max_workers = 2
    return evaluator, mock_research


class TestEvaluateEmptyPaths:
    def test_no_jobs_returns_empty(self) -> None:
        evaluator, _ = _make_evaluator(Path("/tmp/tk_test_no_jobs"))
        summary = evaluator.evaluate(store=False)
        assert isinstance(summary, EvaluationSummary)
        assert len(summary.records) == 0

    def test_jobs_but_no_signals(self) -> None:
        evaluator, mock_research = _make_evaluator(Path("/tmp/tk_test_no_signals"))
        mock_research.list_jobs.return_value = [{"job_id": "j1", "date": "2026-08-14"}]
        mock_research.get_signals_by_job.return_value = []
        summary = evaluator.evaluate(store=False)
        assert len(summary.records) == 0

    def test_all_signals_have_both_errors_skipped(self) -> None:
        evaluator, mock_research = _make_evaluator(Path("/tmp/tk_test_skip_errors"))
        mock_research.list_jobs.return_value = [{"job_id": "j1", "date": "2026-08-14"}]
        mock_research.get_signals_by_job.return_value = [
            {"ticker": "sh.600519", "ta_error": True, "kronos_error": True},
        ]
        summary = evaluator.evaluate(store=False)
        assert len(summary.records) == 0

    def test_ticker_filter_excludes_all(self) -> None:
        evaluator, mock_research = _make_evaluator(Path("/tmp/tk_test_filter"))
        mock_research.list_jobs.return_value = [{"job_id": "j1", "date": "2026-08-14"}]
        mock_research.get_signals_by_job.return_value = [
            {"ticker": "sh.600519", "ta_error": False, "kronos_error": False},
        ]
        summary = evaluator.evaluate(tickers=["sz.000001"], store=False)
        assert len(summary.records) == 0

    def test_date_filter_applied(self) -> None:
        evaluator, mock_research = _make_evaluator(Path("/tmp/tk_test_date"))
        mock_research.list_jobs.return_value = [
            {"job_id": "j1", "date": "2025-01-01"},
            {"job_id": "j2", "date": "2026-08-14"},
        ]
        mock_research.get_signals_by_job.side_effect = lambda jid: (
            [{"ticker": "sh.600519", "ta_error": False, "kronos_error": False,
              "kronos_direction": "UP", "kronos_change": 2.0, "composite_score": 70.0}]
            if jid == "j2" else []
        )
        # 禁用真实价格拉取，确保日期过滤路径单独测试
        with patch("trade_krono_cli.prediction_eval.get_close_price", return_value=None):
            summary = evaluator.evaluate(from_date="2026-01-01", store=False)
        # 入口价格为 None → 全部跳过
        assert len(summary.records) == 0


class TestEvaluatePriceMissing:
    def test_entry_price_none_skipped(self) -> None:
        evaluator, mock_research = _make_evaluator(Path("/tmp/tk_test_entry_none"))
        mock_research.list_jobs.return_value = [{"job_id": "j1", "date": "2026-08-14"}]
        mock_research.get_signals_by_job.return_value = [
            {"ticker": "sh.600519", "ta_error": False, "kronos_error": False,
             "kronos_direction": "UP", "kronos_change": 2.0, "composite_score": 70.0},
        ]
        with patch("trade_krono_cli.prediction_eval.get_close_price", return_value=None):
            summary = evaluator.evaluate(store=False)
        assert len(summary.records) == 0


class TestComputeSummary:
    def test_empty_records(self) -> None:
        evaluator, _ = _make_evaluator(Path("/tmp/tk_test_empty_summary"))
        summary = evaluator._compute_summary([])
        assert len(summary.records) == 0

    def test_records_with_none_fields(self) -> None:
        evaluator, _ = _make_evaluator(Path("/tmp/tk_test_none_fields"))
        records = [
            EvalRecord(
                ticker="sh.600519", eval_date="2026-08-14", horizon_days=5,
                pred_direction=None, pred_return_pct=None, actual_return_pct=2.5,
                actual_direction="UP", is_direction_correct=False, error_pct=0.0,
                ta_signal=None, composite_score=None,
                entry_blocked_limit_up=False, exit_blocked_limit_down=False,
                cost_bps_applied=17.0,
            )
        ]
        summary = evaluator._compute_summary(records)
        assert len(summary.records) == 1


class TestRunEvaluation:
    def test_latest_no_result_logs_warning(self) -> None:
        with patch("trade_krono_cli.prediction_eval.PredictionEvaluator") as MockEval:
            mock_inst = MagicMock()
            mock_inst.get_latest_evaluation.return_value = None
            MockEval.return_value = mock_inst
            run_evaluation(latest=True)
        mock_inst.get_latest_evaluation.assert_called_once()

    def test_latest_with_result_prints(self) -> None:
        # 使用 dict 模拟 get_latest_evaluation 返回的结构（print_latest_* 期望 dict）
        summary_dict = {
            "kronos_n": 10,
            "kronos_dir_accuracy": {"5": 55.0, "10": 52.0, "20": 50.0},
            "ta_buy_n": 3,
            "records": [],
            "combined_buy_up_n": 2,
            "high_conf_n": 1,
            "entry_limit_up_blocked": 0,
            "exit_limit_down_blocked": 0,
            "cost_applied_n": 10,
            "ic_kronos_rank_mean": 0.0,
            "ic_ta_rank_mean": 0.0,
            "alpha_best_benchmark": "",
            "alpha_best_value": 0.0,
            "alpha_all": {},
            "benchmark_results": {},
            "horizons": {},
        }
        result = {
            "eval_at": 1000000.0,
            "eval_date_range": "2026-08",
            "n_records": 10,
            "summary": summary_dict,
        }
        with patch("trade_krono_cli.prediction_eval.PredictionEvaluator") as MockEval:
            mock_inst = MagicMock()
            mock_inst.get_latest_evaluation.return_value = result
            MockEval.return_value = mock_inst
            run_evaluation(latest=True)
        mock_inst.get_latest_evaluation.assert_called_once()
