"""测试预测评估模块 (prediction_eval.py)。"""

from unittest.mock import MagicMock, patch

import pytest

from trade_krono_cli.prediction_eval import (
    EvalRecord,
    EvaluationSummary,
    PredictionEvaluator,
    _apply_roundtrip_cost,
    _calc_return,
    _get_close_price,
    _is_price_at_limit,
    run_evaluation,
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


def test_store_summary_writes_to_db(tmp_path):
    """_store_summary 应能写入 evaluation_results 表而不崩溃。"""
    import sqlite3

    from trade_krono_cli.prediction_eval import (
        EvalRecord,
        PredictionEvaluator,
    )
    from trade_krono_cli.research_db import ResearchDatabase

    db = tmp_path / "store_test.db"
    evaluator = PredictionEvaluator.__new__(PredictionEvaluator)
    evaluator._research = ResearchDatabase(db_path=db)
    evaluator.HORIZONS = [5, 10, 20]

    # 构造有数据的 summary
    records = []
    for i in range(3):
        records.append(
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
        )
    summary = evaluator._compute_summary(records)

    # 调用 _store_summary — 之前会因 AttributeError 崩溃
    evaluator._store_summary(summary, "2026-01-01")

    # 验证记录已写入
    with sqlite3.connect(db) as conn:
        row = conn.execute(
            "SELECT n_records, kronos_acc_5d, ta_buy_wr_5d FROM evaluation_results"
        ).fetchone()
    assert row is not None
    assert row[0] == 3
    assert row[1] == pytest.approx(100.0, abs=0.1)
    assert row[2] == pytest.approx(100.0, abs=0.1)


def test_evaluate_store_true_paths_through_store_summary(tmp_path):
    """evaluate(store=True) 完整路径不应崩溃。"""
    from trade_krono_cli.prediction_eval import PredictionEvaluator
    from trade_krono_cli.research_db import ResearchDatabase

    db = tmp_path / "eval_store.db"
    research = ResearchDatabase(db_path=db)

    # 创建一个 job 并插入信号，使 evaluate() 有数据可处理
    job_id = research.create_job("2026-01-01", ["sh.600519"])
    # 直接插入一条信号记录（模拟 pipeline 已写入）
    import sqlite3

    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO signals (job_id, ticker, rank, composite_score, "
            " ta_signal, ta_confidence, ta_reasoning, kronos_direction, "
            " kronos_change, ta_error, kronos_error) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (job_id, "sh.600519", 1, 80.0, "BUY", 85.0, "test thesis", "UP", 3.0, None, None),
        )
        conn.commit()

    evaluator = PredictionEvaluator.__new__(PredictionEvaluator)
    evaluator._research = research
    evaluator.HORIZONS = [5, 10, 20]

    # evaluate(store=True) 会走 _store_summary；
    # 由于没有实际价格数据，返回空 summary 但不应 AttributeError
    summary = evaluator.evaluate(store=True)
    assert isinstance(summary, type(evaluator._compute_summary([])))


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


def test_get_latest_evaluation_no_table(tmp_path):
    """数据库中没有 evaluation_results 表时，返回 None。"""
    from trade_krono_cli.prediction_eval import PredictionEvaluator
    from trade_krono_cli.research_db import ResearchDatabase

    db = tmp_path / "no_eval.db"
    research = ResearchDatabase(db_path=db)
    evaluator = PredictionEvaluator.__new__(PredictionEvaluator)
    evaluator._research = research

    result = evaluator.get_latest_evaluation()
    assert result is None


def test_get_latest_evaluation_with_data(tmp_path):
    """数据库中有评估结果时，应返回正确数据。"""
    import sqlite3

    from trade_krono_cli.prediction_eval import PredictionEvaluator
    from trade_krono_cli.research_db import ResearchDatabase

    db = tmp_path / "has_eval.db"
    research = ResearchDatabase(db_path=db)
    evaluator = PredictionEvaluator.__new__(PredictionEvaluator)
    evaluator._research = research

    # 手动插入一条评估记录
    with sqlite3.connect(db) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS evaluation_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                eval_at REAL NOT NULL,
                eval_date_range TEXT,
                n_records INTEGER NOT NULL,
                kronos_acc_5d REAL,
                kronos_acc_10d REAL,
                kronos_acc_20d REAL,
                ta_buy_wr_5d REAL,
                ta_buy_wr_10d REAL,
                ta_buy_wr_20d REAL,
                combined_wr_5d REAL,
                combined_wr_10d REAL,
                combined_wr_20d REAL,
                high_conf_wr_5d REAL,
                high_conf_wr_10d REAL,
                summary_json TEXT NOT NULL
            )
        """)
        conn.execute(
            "INSERT INTO evaluation_results "
            "(eval_at, eval_date_range, n_records, kronos_acc_5d, summary_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (1700000000.0, "2026-01-01", 5, 60.0, '{"kronos_n": 5}'),
        )
        conn.commit()

    result = evaluator.get_latest_evaluation()
    assert result is not None
    assert result["n_records"] == 5
    assert result["summary"]["kronos_n"] == 5


def test_print_report_empty_summary(caplog):
    """打印空 summary 的报告不应崩溃。"""
    from loguru import logger

    from trade_krono_cli.prediction_eval import PredictionEvaluator

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

    from trade_krono_cli.prediction_eval import PredictionEvaluator

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

    from trade_krono_cli.prediction_eval import PredictionEvaluator

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

    from trade_krono_cli.prediction_eval import PredictionEvaluator

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


class TestIsPriceAtLimit:
    """测试涨跌停价格检测。"""

    def test_limit_up_detection(self):
        """涨停价 = prev_close * 1.10，price >= 涨停价 × 0.999 则拦截。"""
        assert _is_price_at_limit("sh.600519", 110.0, 100.0, "up") is True
        assert _is_price_at_limit("sh.600519", 109.99, 100.0, "up") is True  # 容差
        assert _is_price_at_limit("sh.600519", 109.88, 100.0, "up") is False  # 低于容差

    def test_limit_down_detection(self):
        """跌停价 = prev_close * 0.90，price / limit_down <= 1.001 则拦截。"""
        assert _is_price_at_limit("sh.600519", 90.0, 100.0, "down") is True
        assert _is_price_at_limit("sh.600519", 90.05, 100.0, "down") is True  # 容差内
        assert _is_price_at_limit("sh.600519", 90.20, 100.0, "down") is False  # 超出容差

    def test_gem_limit(self):
        """创业板 ±20%。"""
        assert _is_price_at_limit("sz.300001", 120.0, 100.0, "up") is True
        assert _is_price_at_limit("sz.300001", 80.0, 100.0, "down") is True

    def test_no_prev_close(self):
        """prev_close 为 None 时不过滤。"""
        assert _is_price_at_limit("sh.600519", 110.0, None, "up") is False
        assert _is_price_at_limit("sh.600519", 110.0, 0, "up") is False

    def test_disabled_limit_check(self):
        """disable limit check 时不拦截。"""
        from trade_krono_cli.constraints_config import ConstraintConfig

        cfg = ConstraintConfig(enable_limit_check=False)
        from trade_krono_cli.trading_constraints import compute_limit_prices

        up, down = compute_limit_prices(100.0, "sh.600519", config=cfg)
        assert up is None  # 禁用时返回 None


class TestApplyRoundtripCost:
    """测试交易成本扣减。"""

    def test_cost_deducted(self):
        """17bps 成本应从收益中扣减。"""
        result = _apply_roundtrip_cost(5.0)
        assert result == pytest.approx(4.83, abs=0.01)

    def test_zero_return(self):
        assert _apply_roundtrip_cost(0.0) == pytest.approx(-0.17, abs=0.01)

    def test_custom_bps(self):
        assert _apply_roundtrip_cost(3.0, cost_bps=10.0) == pytest.approx(2.9, abs=0.01)


class TestEvalRecordWithConstraints:
    """测试新增的约束字段。"""

    def test_record_with_constraints(self):
        r = EvalRecord(
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
            entry_blocked_limit_up=False,
            exit_blocked_limit_down=False,
            cost_bps_applied=17.0,
        )
        assert r.cost_bps_applied == 17.0
        assert r.entry_blocked_limit_up is False
        assert r.exit_blocked_limit_down is False

    def test_summary_tracks_constraints(self):
        s = EvaluationSummary()
        s.entry_limit_up_blocked = 3
        s.exit_limit_down_blocked = 1
        s.cost_applied_n = 10
        assert s.entry_limit_up_blocked == 3
        assert s.cost_applied_n == 10


class TestEvaluateConstraintAware:
    """测试 evaluate() 对约束的处理逻辑。"""

    def test_limit_up_entry_skipped(self, tmp_path):
        """买入日涨停 → 该记录被跳过，不计入 eval_records。"""
        from trade_krono_cli.prediction_eval import PredictionEvaluator
        from trade_krono_cli.research_db import ResearchDatabase

        db = tmp_path / "constraint_eval.db"
        research = ResearchDatabase(db_path=db)

        job_id = research.create_job("2026-01-01", ["sh.600519"])
        import sqlite3

        with sqlite3.connect(db) as conn:
            conn.execute(
                "INSERT INTO signals (job_id, ticker, rank, composite_score, "
                " ta_signal, ta_confidence, ta_reasoning, kronos_direction, "
                " kronos_change, ta_error, kronos_error) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (job_id, "sh.600519", 1, 80.0, "BUY", 85.0, "test", "UP", 3.0, None, None),
            )
            conn.commit()

        evaluator = PredictionEvaluator.__new__(PredictionEvaluator)
        evaluator._research = research
        evaluator.HORIZONS = [5]

        def fake_fetch_kline(ticker, start, end, frequency="d", use_cache=True):
            import pandas as pd

            # 模拟：前一日(2025-12-31) close=100，eval_date(2026-01-01) close=110（涨停）
            # 不包含 horizon 退出日，避免 iloc[-2] 指向错误日期
            return pd.DataFrame(
                {
                    "timestamps": ["2025-12-31", "2026-01-01"],
                    "open": [100.0, 100.0],
                    "high": [100.0, 110.0],
                    "low": [99.0, 108.0],
                    "close": [100.0, 110.0],
                    "volume": [1000.0, 5000.0],
                    "amount": [100000.0, 550000.0],
                }
            )

        _call_idx = [0]

        def fake_get_close(ticker, date_str):
            if "2026-01-01" in date_str:
                return 110.0  # 涨停价
            elif "2026-01-06" in date_str:
                return 108.0  # 退出日正常
            return None

        with patch("trade_krono_cli.eval_data.fetch_kline", side_effect=fake_fetch_kline):
            with patch(
                "trade_krono_cli.prediction_eval._get_close_price", side_effect=fake_get_close
            ):
                summary = evaluator.evaluate(store=False)

        # 涨停买入被跳过，eval_records 为空
        assert len(summary.records) == 0
        assert summary.entry_limit_up_blocked == 1
        assert summary.exit_limit_down_blocked == 0

    def test_cost_deducted_in_return(self, tmp_path):
        """正常信号应扣减 17bps 交易成本。"""
        from trade_krono_cli.prediction_eval import PredictionEvaluator
        from trade_krono_cli.research_db import ResearchDatabase

        db = tmp_path / "cost_eval.db"
        research = ResearchDatabase(db_path=db)

        job_id = research.create_job("2026-01-01", ["sh.600519"])
        import sqlite3

        with sqlite3.connect(db) as conn:
            conn.execute(
                "INSERT INTO signals (job_id, ticker, rank, composite_score, "
                " ta_signal, ta_confidence, ta_reasoning, kronos_direction, "
                " kronos_change, ta_error, kronos_error) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (job_id, "sh.600519", 1, 80.0, "BUY", 85.0, "test", "UP", 5.0, None, None),
            )
            conn.commit()

        evaluator = PredictionEvaluator.__new__(PredictionEvaluator)
        evaluator._research = research
        evaluator.HORIZONS = [5]

        entry_called = [False]
        exit_called = [False]

        def fake_get_close(ticker, date_str):
            if "2026-01-01" in date_str:
                entry_called[0] = True
                return 100.0
            elif "2026-01-06" in date_str:
                exit_called[0] = True
                return 105.0
            return None

        # 返回正常 K 线（非涨停）
        def fake_fetch_kline(ticker, start, end, frequency="d", use_cache=True):
            import pandas as pd

            return pd.DataFrame(
                {
                    "timestamps": ["2025-12-31", "2026-01-01", "2026-01-06"],
                    "open": [100.0, 100.0, 100.0],
                    "high": [101.0, 101.0, 106.0],
                    "low": [99.0, 99.0, 99.0],
                    "close": [100.0, 100.0, 105.0],
                    "volume": [1000.0, 1000.0, 1000.0],
                    "amount": [100000.0, 100000.0, 105000.0],
                }
            )

        with patch("trade_krono_cli.eval_data.fetch_kline", side_effect=fake_fetch_kline):
            with patch(
                "trade_krono_cli.prediction_eval._get_close_price", side_effect=fake_get_close
            ):
                summary = evaluator.evaluate(store=False)

        assert len(summary.records) == 1
        r = summary.records[0]
        # 毛收益 = (105-100)/100*100 = 5.0%，扣 17bps = 0.17%
        assert r.actual_return_pct == pytest.approx(4.83, abs=0.01)
        assert r.cost_bps_applied == 17.0
        assert summary.cost_applied_n == 1
        assert summary.entry_limit_up_blocked == 0
