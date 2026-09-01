"""测试 prediction_eval.py 报告输出：print_report、run_evaluation。"""

from unittest.mock import MagicMock, patch

from trade_krono_cli.prediction_eval import (
    EvalRecord,
    EvaluationSummary,
    PredictionEvaluator,
    run_evaluation,
)


def test_print_report_empty_summary(caplog):
    """打印空 summary 的报告不应崩溃。"""
    from loguru import logger


    evaluator = PredictionEvaluator.__new__(PredictionEvaluator)
    evaluator.HORIZONS = [5, 10, 20]
    summary = EvaluationSummary()

    captured: list[str] = []

    def _capture(*args, **kwargs):
        if args:
            captured.append(str(args[0]))

    with patch.object(logger, "info", _capture):
        evaluator.print_report(summary)
    full = "\n".join(captured)
    assert "预测评估报告" in full
    assert "5D 准确率:   0.0%" in full


def test_print_report_with_data(caplog):
    """打印有数据的报告应包含正确的指标。"""
    from loguru import logger


    evaluator = PredictionEvaluator.__new__(PredictionEvaluator)
    evaluator.HORIZONS = [5, 10, 20]

    records = [
        EvalRecord(
            ticker="sh.600519",
            eval_date="2026-01-01",
            horizon_days=5,
            pred_direction="UP",
            pred_return_pct=3.0,
            actual_return_pct=2.5,
            actual_direction="UP",
            is_direction_correct=True,
            error_pct=0.5,
            ta_signal="BUY",
            composite_score=80.0,
        )
    ]
    summary = evaluator._compute_summary(records)

    captured: list[str] = []

    def _capture(*args, **kwargs):
        if args:
            captured.append(str(args[0]))

    with patch.object(logger, "info", _capture):
        evaluator.print_report(summary)
    full = "\n".join(captured)
    assert "100.0%" in full  # 5D 准确率 100%
    assert "综合信号" in full


def test_run_evaluation_no_results(caplog):
    """latest=True 且无评估结果时，应打印提示并返回。"""
    from loguru import logger


    captured: list[str] = []

    def _capture(*args, **kwargs):
        if args:
            captured.append(str(args[0]))

    with patch.object(PredictionEvaluator, "get_latest_evaluation") as mock_get:
        mock_get.return_value = None
        with patch.object(logger, "info", _capture), patch.object(logger, "warning", _capture):
            run_evaluation(latest=True)
    assert "暂无评估结果" in "\n".join(captured)


def test_run_evaluation_with_latest_result(caplog):
    """latest=True 且有评估结果时，应打印结果。"""
    from loguru import logger


    class FakeSummary:
        kronos_n = 3
        ta_buy_n = 1
        combined_buy_up_n = 0
        high_conf_n = 0
        horizons = {
            5: MagicMock(
                kronos_dir_accuracy=60.0,
                ta_buy_win_rate=50.0,
                ta_buy_avg_return=1.5,
                combined_buy_up_win_rate=0.0,
                combined_buy_up_avg_return=0.0,
                high_conf_win_rate=0.0,
                high_conf_avg_return=0.0,
            )
        }
        records = []

        def get(self, key, default=None):
            return getattr(self, key, default)

    captured: list[str] = []

    def _capture(*args, **kwargs):
        if args:
            captured.append(str(args[0]))

    with patch.object(PredictionEvaluator, "get_latest_evaluation") as mock_get:
        fake = MagicMock()
        fake.__getitem__ = lambda self, key: {
            "id": 1,
            "eval_at": 1700000000.0,
            "eval_date_range": "2026-01-01",
            "n_records": 3,
            "summary": FakeSummary(),
        }[key]
        mock_get.return_value = fake
        with patch.object(logger, "info", _capture), patch.object(logger, "warning", _capture):
            run_evaluation(latest=True)
    full = "\n".join(captured)
    assert "最新评估结果" in full
    assert "样本数: 3" in full


# ═══════════════════════════════════════════════════════
# 交易约束感知评估测试
# ═══════════════════════════════════════════════════════


