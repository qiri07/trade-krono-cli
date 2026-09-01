"""测试 prediction_eval.py 核心评估函数：_calc_return、_get_close_price、EvaluationSummary。"""

from unittest.mock import MagicMock, patch

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
    from trade_krono_cli.research_db import ResearchDatabase

    db = tmp_path / "test_eval.db"
    _research = ResearchDatabase(db_path=db)
    evaluator = PredictionEvaluator()
    # 默认使用全局单例，不传 db_path
    assert evaluator is not None
    assert evaluator.HORIZONS == [5, 10, 20]


def test_predict_empty_evaluation(tmp_path):
    """没有历史数据时返回空 Summary。"""
    from trade_krono_cli.research_db import ResearchDatabase

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
        records.append(
            EvalRecord(
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
            )
        )

    summary = evaluator._compute_summary(records)

    m5 = summary.horizons[5]

    # TA BUY 5D: 3 win / 5 total = 60%
    assert m5.ta_buy_win_rate == pytest.approx(60.0, abs=0.1)
    # 平均收益: (2+2+2-1.5-1.5)/5 = 0.6
    assert m5.ta_buy_avg_return == pytest.approx(0.6, abs=0.1)
    assert summary.ta_buy_n == 5

    # 综合信号（TA BUY + Kronos UP）: 只有前3条
    assert m5.combined_buy_up_win_rate == pytest.approx(100.0, abs=0.1)
    assert m5.combined_buy_up_avg_return == pytest.approx(2.0, abs=0.1)
    assert summary.combined_buy_up_n == 3

    # 高置信（score >= 70）: 前3条 score=80
    assert m5.high_conf_win_rate == pytest.approx(100.0, abs=0.1)
    assert summary.high_conf_n == 3

    # Kronos 方向准确率 5D: 3/5 = 60%
    assert m5.kronos_dir_accuracy == pytest.approx(60.0, abs=0.1)
    assert summary.kronos_n == 5


def test_get_close_price_returns_none_on_empty():
    """当 fetch_kline 返回空 DataFrame 时，应返回 None。"""
    with patch("trade_krono_cli.eval_data.fetch_kline") as mock_fetch:
        mock_fetch.return_value = MagicMock()
        mock_fetch.return_value.empty = True
        result = _get_close_price("sh.600519", "2026-08-11")
    assert result is None


def test_get_close_price_returns_none_on_error():
    """fetch_kline 抛异常时，应返回 None。"""
    with patch("trade_krono_cli.eval_data.fetch_kline") as mock_fetch:
        mock_fetch.side_effect = RuntimeError("network error")
        result = _get_close_price("sh.600519", "2026-08-11")
    assert result is None


def test_get_close_price_fallback_to_nearest():
    """精确日期无数据时，应回退到最近交易日。"""
    import pandas as pd

    with patch("trade_krono_cli.eval_data.fetch_kline") as mock_fetch:
        df = pd.DataFrame(
            {
                "timestamps": ["2026-08-08", "2026-08-12"],
                "close": [100.0, 105.0],
            }
        )
        mock_fetch.return_value = df
        result = _get_close_price("sh.600519", "2026-08-11")
    # 应回退到最近的收盘价 105.0
    assert result == 105.0


def test_compute_summary_empty_horizon():
    """记录跨多个 horizon 时的正确分组。"""
    from trade_krono_cli.prediction_eval import PredictionEvaluator

    evaluator = PredictionEvaluator.__new__(PredictionEvaluator)
    evaluator.HORIZONS = [5, 10, 20]

    records = []
    # 5D 记录
    for i in range(2):
        records.append(
            EvalRecord(
                ticker="sh.600519",
                eval_date="2026-01-01",
                horizon_days=5,
                pred_direction="UP",
                pred_return_pct=None,
                actual_return_pct=3.0,
                actual_direction="UP",
                is_direction_correct=True,
                error_pct=0.0,
                ta_signal="BUY",
                composite_score=75.0,
            )
        )
    # 10D 记录
    records.append(
        EvalRecord(
            ticker="sh.600519",
            eval_date="2026-01-01",
            horizon_days=10,
            pred_direction="DOWN",
            pred_return_pct=None,
            actual_return_pct=-2.0,
            actual_direction="DOWN",
            is_direction_correct=True,
            error_pct=0.0,
            ta_signal=None,
            composite_score=None,
        )
    )

    summary = evaluator._compute_summary(records)
    assert 5 in summary.horizons
    assert 10 in summary.horizons
    assert 20 not in summary.horizons
    assert summary.horizons[5].kronos_dir_accuracy == pytest.approx(100.0, abs=0.1)
    assert summary.horizons[10].kronos_dir_accuracy == pytest.approx(100.0, abs=0.1)


def test_compute_summary_no_pred_direction():
    """pred_direction 为 None 时，Kronos 方向准确率不计入。"""
    from trade_krono_cli.prediction_eval import PredictionEvaluator

    evaluator = PredictionEvaluator.__new__(PredictionEvaluator)
    evaluator.HORIZONS = [5, 10, 20]

    records = [
        EvalRecord(
            ticker="sh.600519",
            eval_date="2026-01-01",
            horizon_days=5,
            pred_direction=None,
            pred_return_pct=None,
            actual_return_pct=3.0,
            actual_direction="UP",
            is_direction_correct=False,
            error_pct=0.0,
            ta_signal="BUY",
            composite_score=70.0,
        )
    ]

    summary = evaluator._compute_summary(records)
    m5 = summary.horizons[5]
    # pred_direction 为 None，kronos_n 应为 0，准确率应为 0
    assert summary.kronos_n == 0
    assert m5.kronos_dir_accuracy == 0.0


def test_compute_summary_flat_direction():
    """FLAT 方向的 Kronos 预测仍计入 kronos_n，但方向不判定为正确。"""
    from trade_krono_cli.prediction_eval import PredictionEvaluator

    evaluator = PredictionEvaluator.__new__(PredictionEvaluator)
    evaluator.HORIZONS = [5, 10, 20]

    records = [
        EvalRecord(
            ticker="sh.600519",
            eval_date="2026-01-01",
            horizon_days=5,
            pred_direction="FLAT",
            pred_return_pct=None,
            actual_return_pct=3.0,
            actual_direction="UP",
            is_direction_correct=False,
            error_pct=0.0,
            ta_signal=None,
            composite_score=None,
        )
    ]

    summary = evaluator._compute_summary(records)
    m5 = summary.horizons[5]
    # FLAT 仍计入 kronos_n，但 is_direction_correct=False
    assert summary.kronos_n == 1
    assert m5.kronos_dir_accuracy == 0.0


def test_compute_summary_high_conf_threshold():
    """composite_score >= 70 才计入高置信。"""
    from trade_krono_cli.prediction_eval import PredictionEvaluator

    evaluator = PredictionEvaluator.__new__(PredictionEvaluator)
    evaluator.HORIZONS = [5, 10, 20]

    records = [
        EvalRecord(
            ticker="sh.600519",
            eval_date="2026-01-01",
            horizon_days=5,
            pred_direction="UP",
            pred_return_pct=None,
            actual_return_pct=2.0,
            actual_direction="UP",
            is_direction_correct=True,
            error_pct=0.0,
            ta_signal="BUY",
            composite_score=70.0,
        ),
        EvalRecord(
            ticker="sh.600519",
            eval_date="2026-01-01",
            horizon_days=5,
            pred_direction="UP",
            pred_return_pct=None,
            actual_return_pct=2.0,
            actual_direction="UP",
            is_direction_correct=True,
            error_pct=0.0,
            ta_signal="BUY",
            composite_score=69.0,
        ),
        EvalRecord(
            ticker="sh.600519",
            eval_date="2026-01-01",
            horizon_days=5,
            pred_direction="UP",
            pred_return_pct=None,
            actual_return_pct=2.0,
            actual_direction="UP",
            is_direction_correct=True,
            error_pct=0.0,
            ta_signal="BUY",
            composite_score=None,
        ),
    ]

    summary = evaluator._compute_summary(records)
    m5 = summary.horizons[5]
    # 只有 score=70 的那条计入高置信
    assert summary.high_conf_n == 1
    assert m5.high_conf_win_rate == pytest.approx(100.0, abs=0.1)
