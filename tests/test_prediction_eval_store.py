"""测试 prediction_eval.py 持久化：store_summary、get_latest_evaluation。"""


import pytest

from trade_krono_cli.prediction_eval import (
    EvalRecord,
    PredictionEvaluator,
)


def test_store_summary_writes_to_db(tmp_path):
    """_store_summary 应能写入 evaluation_results 表而不崩溃。"""
    import sqlite3

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


def test_get_latest_evaluation_no_table(tmp_path):
    """数据库中没有 evaluation_results 表时，返回 None。"""
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


