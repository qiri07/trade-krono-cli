"""测试 ResearchDatabase — Walk-Forward、Snapshots、Experiments 子模块。"""

import pytest

from trade_krono_cli.research_db import ResearchDatabase


@pytest.fixture
def db(tmp_path):
    return ResearchDatabase(db_path=tmp_path / "test.db")


# ── Snapshots ─────────────────────────────────────────────────────────────────


class TestSnapshots:
    def test_insert_and_get_snapshot(self, db):
        db.insert_data_snapshot(
            snapshot_id="snap-1",
            cut_date="2026-01-01",
            effective_cut="2025-12-31",
            sources=[{"name": "baostock"}],
            description="test snapshot",
        )
        result = db.get_data_snapshot("snap-1")
        assert result is not None
        assert result["snapshot_id"] == "snap-1"
        assert result["cut_date"] == "2026-01-01"
        assert result["sources"] == [{"name": "baostock"}]
        assert result["description"] == "test snapshot"

    def test_get_snapshot_not_found(self, db):
        assert db.get_data_snapshot("nonexistent") is None

    def test_insert_overwrites_snapshot(self, db):
        db.insert_data_snapshot(
            snapshot_id="snap-1",
            cut_date="2026-01-01",
            effective_cut="2025-12-31",
            sources=[{"name": "a"}],
        )
        db.insert_data_snapshot(
            snapshot_id="snap-1",
            cut_date="2026-02-01",
            effective_cut="2026-01-31",
            sources=[{"name": "b"}],
        )
        result = db.get_data_snapshot("snap-1")
        assert result["cut_date"] == "2026-02-01"
        assert result["sources"] == [{"name": "b"}]


# ── Walk-Forward ──────────────────────────────────────────────────────────────


class TestWalkforward:
    def test_insert_and_get_walkforward(self, db):
        db.insert_walkforward_run(
            run_id="wf-1",
            experiment_id="exp-1",
            ticker="600519",
            config={"pred_len": 30},
            total_windows=10,
            valid_windows=8,
            win_rate=0.6,
            avg_return=0.02,
            sharpe_annual=1.5,
            n_records=100,
            elapsed_sec=5.0,
        )
        rows = db.get_walkforward_runs()
        assert len(rows) == 1
        r = rows[0]
        assert r["run_id"] == "wf-1"
        assert r["ticker"] == "600519"
        assert r["win_rate"] == 0.6
        assert r["sharpe_annual"] == 1.5

    def test_get_walkforward_filtered(self, db):
        db.insert_walkforward_run(
            run_id="wf-1",
            experiment_id="exp-1",
            ticker="600519",
            config={},
            total_windows=5,
            valid_windows=4,
            win_rate=0.8,
            avg_return=0.01,
            sharpe_annual=1.0,
            n_records=50,
            elapsed_sec=2.0,
        )
        db.insert_walkforward_run(
            run_id="wf-2",
            experiment_id="exp-2",
            ticker="000858",
            config={},
            total_windows=3,
            valid_windows=2,
            win_rate=0.5,
            avg_return=0.005,
            sharpe_annual=0.5,
            n_records=30,
            elapsed_sec=1.0,
        )
        rows = db.get_walkforward_runs(ticker="000858")
        assert len(rows) == 1
        assert rows[0]["ticker"] == "000858"

    def test_get_walkforward_by_experiment(self, db):
        db.insert_walkforward_run(
            run_id="wf-1",
            experiment_id="exp-A",
            ticker="600519",
            config={},
            total_windows=5,
            valid_windows=4,
            win_rate=0.8,
            avg_return=0.01,
            sharpe_annual=1.0,
            n_records=50,
            elapsed_sec=2.0,
        )
        rows = db.get_walkforward_runs(experiment_id="exp-A")
        assert len(rows) == 1
        assert rows[0]["experiment_id"] == "exp-A"

    def test_get_walkforward_not_found(self, db):
        assert db.get_walkforward_runs(ticker="ZZZZZZ") == []


# ── Experiments ───────────────────────────────────────────────────────────────


class TestExperiments:
    def test_insert_and_get_experiment(self, db):
        db.insert_experiment(
            experiment_id="exp-1",
            full_id="full-1",
            experiment_type="ablation",
            hypothesis={"var": "lr", "value": 0.01},
            description="test exp",
            config={"model": "kronos-base"},
            passed=True,
        )
        result = db.get_experiment("exp-1")
        assert result is not None
        assert result["experiment_id"] == "exp-1"
        assert result["experiment_type"] == "ablation"
        assert result["hypothesis"] == {"var": "lr", "value": 0.01}
        assert result["passed"] is True

    def test_get_experiment_not_found(self, db):
        assert db.get_experiment("nonexistent") is None

    def test_list_experiments(self, db):
        db.insert_experiment(
            experiment_id="exp-1",
            full_id="full-1",
            experiment_type="ablation",
            hypothesis={},
            passed=True,
        )
        db.insert_experiment(
            experiment_id="exp-2",
            full_id="full-2",
            experiment_type="comparison",
            hypothesis={},
            passed=False,
        )
        rows = db.list_experiments()
        assert len(rows) == 2

    def test_list_experiments_filtered_by_type(self, db):
        db.insert_experiment(
            experiment_id="exp-1",
            full_id="full-1",
            experiment_type="ablation",
            hypothesis={},
            passed=True,
        )
        db.insert_experiment(
            experiment_id="exp-2",
            full_id="full-2",
            experiment_type="comparison",
            hypothesis={},
            passed=True,
        )
        rows = db.list_experiments(experiment_type="ablation")
        assert len(rows) == 1
        assert rows[0]["experiment_id"] == "exp-1"

    def test_list_experiments_only_passed(self, db):
        db.insert_experiment(
            experiment_id="exp-1",
            full_id="full-1",
            experiment_type="ablation",
            hypothesis={},
            passed=True,
        )
        db.insert_experiment(
            experiment_id="exp-2",
            full_id="full-2",
            experiment_type="ablation",
            hypothesis={},
            passed=False,
        )
        rows = db.list_experiments(only_passed=True)
        assert len(rows) == 1
        assert rows[0]["experiment_id"] == "exp-1"

    def test_insert_with_none_values(self, db):
        db.insert_experiment(
            experiment_id="exp-1",
            full_id="full-1",
            experiment_type="ablation",
            hypothesis={"a": 1},
            passed=None,
        )
        result = db.get_experiment("exp-1")
        assert result["passed"] is None
        assert result["config"] == {}
        assert result["run_ids"] == []
