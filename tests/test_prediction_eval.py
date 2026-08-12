"""测试预测评估模块 (prediction_eval.py)。"""
import pytest
from trade_krono_cli.prediction_eval import (
    EvalRecord,
    EvaluationSummary,
    PredictionEvaluator,
    _calc_return,
    _get_close_price,
)


def test_calc_return():
    assert abs(_calc_return(100.0, 105.0) - 5.0) < 0.01
    assert abs(_calc_return(100.0, 95.0) - (-5.0)) < 0.01
    assert _calc_return(0, 100) == 0.0
    assert _calc_return(-1, 100) == 0.0


def test_eval_record_creation():
    r = EvalRecord(
        ticker="sh.600519",
        eval_date="2026-08-11",
        horizon_days=5,
        pred_direction="UP",
        pred_return_pct=5.0,
        actual_return_pct=3.2,
        actual_direction="UP",
        is_direction_correct=True,
        error_pct=1.8,
        ta_signal="BUY",
        composite_score=82.0,
    )
    assert r.ticker == "sh.600519"
    assert r.is_direction_correct is True
    assert r.error_pct == 1.8


def test_evaluation_summary_defaults():
    s = EvaluationSummary()
    assert s.kronos_n == 0
    assert s.ta_buy_n == 0
    assert s.combined_buy_up_n == 0
    assert s.high_conf_n == 0
    assert s.ta_hold_n == 0
    # 默认值都是 0，不会引起 division by zero
    assert 5 not in s.horizons
    assert isinstance(s.records, list)


def test_prediction_evaluator_init(tmp_path):
    """PredictionEvaluator 可以正常初始化。"""
    from trade_krono_cli.cache import ResearchDatabase
    db = tmp_path / "test_eval.db"
    research = ResearchDatabase(db_path=db)
    evaluator = PredictionEvaluator()
    # 默认使用全局单例，不传 db_path
    assert evaluator is not None
    assert evaluator.HORIZONS == [5, 10, 20]


def test_predict_empty_evaluation(tmp_path):
    """没有历史数据时返回空 Summary。"""
    from trade_krono_cli.cache import ResearchDatabase
    db = tmp_path / "empty_eval.db"
    research = ResearchDatabase(db_path=db)

    # 替换 evaluator 的 _research
    from trade_krono_cli.prediction_eval import PredictionEvaluator
    evaluator = PredictionEvaluator.__new__(PredictionEvaluator)
    evaluator._research = research
    evaluator.HORIZONS = [5, 10, 20]

    summary = evaluator.evaluate()
    assert summary.kronos_n == 0
    assert summary.records == []


def test_compute_summary_with_mock_records():
    """用 mock 记录验证统计计算逻辑。"""
    from trade_krono_cli.prediction_eval import PredictionEvaluator

    evaluator = PredictionEvaluator.__new__(PredictionEvaluator)
    evaluator.HORIZONS = [5, 10, 20]

    # 构造 mock 记录：5D BUY 信号，3 胜 2 负
    records = []
    for i in range(5):
        records.append(EvalRecord(
            ticker="sh.600519",
            eval_date="2026-01-01",
            horizon_days=5,
            pred_direction="UP" if i < 3 else "DOWN",
            pred_return_pct=None,
            actual_return_pct=2.0 if i < 3 else -1.5,
            actual_direction="UP" if i < 3 else "DOWN",
            is_direction_correct=(i < 3),
            error_pct=0.0,
            ta_signal="BUY",
            composite_score=80.0 if i < 3 else 60.0,
        ))

    summary = evaluator._compute_summary(records)

    m5 = summary.horizons[5]

    # TA BUY 5D: 3 win / 5 total = 60%
    assert m5.ta_buy_win_rate == pytest.approx(60.0, abs=0.1)
    # 平均收益: (2+2+2-1.5-1.5)/5 = 0.6
    assert m5.ta_buy_avg_return == pytest.approx(0.6, abs=0.1)
    assert summary.ta_buy_n == 5

    # 综合信号（TA BUY + Kronos UP）: 只有前3条
    assert m5.combined_buy_up_win_rate == pytest.approx(
        100.0, abs=0.1
    )
    assert m5.combined_buy_up_avg_return == pytest.approx(2.0, abs=0.1)
    assert summary.combined_buy_up_n == 3

    # 高置信（score >= 70）: 前3条 score=80
    assert m5.high_conf_win_rate == pytest.approx(
        100.0, abs=0.1
    )
    assert summary.high_conf_n == 3

    # Kronos 方向准确率 5D: 3/5 = 60%
    assert m5.kronos_dir_accuracy == pytest.approx(60.0, abs=0.1)
    assert summary.kronos_n == 5
