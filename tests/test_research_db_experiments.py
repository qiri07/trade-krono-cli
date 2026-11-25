"""trade_krono_cli.research_db.experiments — Experiments 表读写单元测试。

使用独立临时数据库（tmp_path），避免跨测试污染。
"""

from __future__ import annotations

import pytest

from trade_krono_cli.research_db import ResearchDatabase


@pytest.fixture
def db(tmp_path):
    return ResearchDatabase(db_path=tmp_path / "research.db")


def test_insert_and_get_experiment(db) -> None:
    db.insert_experiment(
        experiment_id="exp_001",
        full_id="exp_001_v1",
        experiment_type="backtest",
        hypothesis={"ma_fast": 5, "ma_slow": 13},
        description="双均线金叉回测",
        config={"lookback": 200},
        data_snapshot_id="snap_001",
        run_ids=["run_a", "run_b"],
        result_summary={"win_rate": 0.6, "avg_return": 0.05},
        passed=True,
        notes="初步回测通过",
    )

    row = db.get_experiment("exp_001")
    assert row is not None
    assert row["experiment_id"] == "exp_001"
    assert row["full_id"] == "exp_001_v1"
    assert row["experiment_type"] == "backtest"
    assert row["hypothesis"] == {"ma_fast": 5, "ma_slow": 13}
    assert row["description"] == "双均线金叉回测"
    assert row["config"] == {"lookback": 200}
    assert row["data_snapshot_id"] == "snap_001"
    assert row["run_ids"] == ["run_a", "run_b"]
    assert row["result_summary"] == {"win_rate": 0.6, "avg_return": 0.05}
    assert row["passed"] is True
    assert row["notes"] == "初步回测通过"
    assert isinstance(row["created_at"], float)


def test_get_experiment_missing_returns_none(db) -> None:
    assert db.get_experiment("not_exist") is None


def test_insert_experiment_replace(db) -> None:
    db.insert_experiment(
        experiment_id="exp_002",
        full_id="exp_002_v1",
        experiment_type="grid_search",
        hypothesis={"a": 1},
        description="first",
    )
    db.insert_experiment(
        experiment_id="exp_002",
        full_id="exp_002_v2",
        experiment_type="grid_search",
        hypothesis={"a": 2},
        description="second",
        passed=False,
    )
    row = db.get_experiment("exp_002")
    assert row["description"] == "second"
    assert row["hypothesis"] == {"a": 2}
    assert row["passed"] is False


def test_insert_experiment_passed_none(db) -> None:
    db.insert_experiment(
        experiment_id="exp_003",
        full_id="exp_003_v1",
        experiment_type="walkforward",
        hypothesis={},
        passed=None,
    )
    row = db.get_experiment("exp_003")
    assert row["passed"] is None


def test_list_experiments_default(db) -> None:
    db.insert_experiment("e1", "e1_v1", "backtest", {"x": 1}, "desc 1")
    db.insert_experiment("e2", "e2_v1", "backtest", {"x": 2}, "desc 2")
    db.insert_experiment("e3", "e3_v1", "grid_search", {"x": 3}, "desc 3")
    rows = db.list_experiments()
    assert len(rows) == 3
    # 最近创建在前（ORDER BY created_at DESC）
    assert rows[0]["experiment_id"] == "e3"


def test_list_experiments_filter_by_type(db) -> None:
    db.insert_experiment("e1", "e1_v1", "backtest", {"x": 1}, "desc 1")
    db.insert_experiment("e2", "e2_v1", "grid_search", {"x": 2}, "desc 2")
    rows = db.list_experiments(experiment_type="backtest")
    assert len(rows) == 1
    assert rows[0]["experiment_id"] == "e1"


def test_list_experiments_only_passed(db) -> None:
    db.insert_experiment("e1", "e1_v1", "backtest", {"x": 1}, passed=True)
    db.insert_experiment("e2", "e2_v1", "backtest", {"x": 2}, passed=False)
    db.insert_experiment("e3", "e3_v1", "backtest", {"x": 3}, passed=None)
    rows = db.list_experiments(only_passed=True)
    assert len(rows) == 1
    assert rows[0]["experiment_id"] == "e1"


def test_list_experiments_limit(db) -> None:
    for i in range(5):
        db.insert_experiment(f"e{i}", f"e{i}_v1", "backtest", {"x": i}, f"desc {i}")
    rows = db.list_experiments(limit=2)
    assert len(rows) == 2

    # JSON 往返正确性（get_experiment 已返回解析后的 dict）
    stored = db.get_experiment("e4")["hypothesis"] if db.get_experiment("e4") else {}
    assert stored == {"x": 4}


def test_list_experiments_empty(db) -> None:
    assert db.list_experiments() == []
