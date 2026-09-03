"""Tests for trade_krono_cli.experiment_registry.

覆盖 ExperimentRecord 和 ExperimentRegistry 的完整生命周期。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trade_krono_cli.domain.experiment import Hypothesis
from trade_krono_cli.domain.types import ExperimentType
from trade_krono_cli.experiment_registry import (
    ExperimentRecord,
    ExperimentRegistry,
    register_alpha_experiment,
)

# ═══════════════════════════════════════════════════════
#  ExperimentRecord
# ═══════════════════════════════════════════════════════


class TestExperimentRecord:
    def test_full_id_is_deterministic(self) -> None:
        h = Hypothesis(
            statement="win_rate > 55",
            prediction="win_rate > 55",
            falsification="win_rate <= 55",
            metric="win_rate",
            threshold=55.0,
            direction=">",
        )
        fixed_time = "2026-09-02T12:00:00+00:00"
        r1 = ExperimentRecord(
            experiment_id="exp_001",
            experiment_type=ExperimentType.ALPHA,
            hypothesis=h,
            created_at=fixed_time,
        )
        r2 = ExperimentRecord(
            experiment_id="exp_001",
            experiment_type=ExperimentType.ALPHA,
            hypothesis=h,
            created_at=fixed_time,
        )
        assert r1.full_id == r2.full_id

    def test_full_id_differs_on_change(self) -> None:
        h1 = Hypothesis(
            statement="win_rate > 55",
            prediction="win_rate > 55",
            falsification="win_rate <= 55",
            metric="win_rate",
            threshold=55.0,
            direction=">",
        )
        h2 = Hypothesis(
            statement="win_rate > 60",
            prediction="win_rate > 60",
            falsification="win_rate <= 60",
            metric="win_rate",
            threshold=60.0,
            direction=">",
        )
        r1 = ExperimentRecord(
            experiment_id="exp_001", experiment_type=ExperimentType.ALPHA, hypothesis=h1
        )
        r2 = ExperimentRecord(
            experiment_id="exp_001", experiment_type=ExperimentType.ALPHA, hypothesis=h2
        )
        assert r1.full_id != r2.full_id

    def test_full_id_is_32_chars(self) -> None:
        h = Hypothesis(
            statement="test",
            prediction="test",
            falsification="test",
            metric="win_rate",
            threshold=55.0,
            direction=">",
        )
        r = ExperimentRecord(experiment_id="x", experiment_type=ExperimentType.ALPHA, hypothesis=h)
        assert len(r.full_id) == 32

    def test_evaluate_pass(self) -> None:
        h = Hypothesis(
            statement="win_rate > 55",
            prediction="win_rate > 55",
            falsification="win_rate <= 55",
            metric="win_rate",
            threshold=55.0,
            direction=">",
        )
        r = ExperimentRecord(
            experiment_id="exp_001",
            experiment_type=ExperimentType.ALPHA,
            hypothesis=h,
            result_summary={"win_rate": 60.0},
        )
        passed, expl = r.evaluate()
        assert passed is True
        assert "60.0" in expl

    def test_evaluate_fail(self) -> None:
        h = Hypothesis(
            statement="win_rate > 55",
            prediction="win_rate > 55",
            falsification="win_rate <= 55",
            metric="win_rate",
            threshold=55.0,
            direction=">",
        )
        r = ExperimentRecord(
            experiment_id="exp_001",
            experiment_type=ExperimentType.ALPHA,
            hypothesis=h,
            result_summary={"win_rate": 50.0},
        )
        passed, expl = r.evaluate()
        assert passed is False

    def test_evaluate_missing_metric(self) -> None:
        h = Hypothesis(
            statement="win_rate > 55",
            prediction="win_rate > 55",
            falsification="win_rate <= 55",
            metric="win_rate",
            threshold=55.0,
            direction=">",
        )
        r = ExperimentRecord(
            experiment_id="exp_001",
            experiment_type=ExperimentType.ALPHA,
            hypothesis=h,
            result_summary={},
        )
        passed, expl = r.evaluate()
        assert passed is False
        assert "win_rate" in expl

    def test_to_dict_roundtrip(self) -> None:
        h = Hypothesis(
            statement="alpha > 0",
            prediction="alpha > 0",
            falsification="alpha <= 0",
            metric="alpha",
            threshold=0.0,
            direction=">",
        )
        r = ExperimentRecord(
            experiment_id="exp_001",
            experiment_type=ExperimentType.ALPHA,
            hypothesis=h,
            description="test desc",
            config={"lr": 0.01},
            result_summary={"win_rate": 58.0},
            passed=True,
            notes="good run",
        )
        d = r.to_dict()
        assert d["experiment_id"] == "exp_001"
        assert d["hypothesis"]["statement"] == "alpha > 0"
        assert d["passed"] is True

        restored = ExperimentRecord.from_dict(d)
        assert restored.experiment_id == r.experiment_id
        assert restored.hypothesis.statement == r.hypothesis.statement
        assert restored.passed == r.passed

    def test_from_dict_defaults(self) -> None:
        d = {"experiment_id": "exp_002", "experiment_type": "model", "hypothesis": {}}
        r = ExperimentRecord.from_dict(d)
        assert r.experiment_id == "exp_002"
        assert r.hypothesis.statement == ""
        assert r.passed is None


# ═══════════════════════════════════════════════════════
#  ExperimentRegistry
# ═══════════════════════════════════════════════════════


class TestExperimentRegistry:
    def test_register(self) -> None:
        reg = ExperimentRegistry()
        h = Hypothesis(
            statement="win_rate > 55",
            prediction="win_rate > 55",
            falsification="win_rate <= 55",
            metric="win_rate",
            threshold=55.0,
            direction=">",
        )
        rec = reg.register("exp_001", h)
        assert rec.experiment_id == "exp_001"
        assert reg.get("exp_001") is rec

    def test_register_duplicate_overwrites(self) -> None:
        reg = ExperimentRegistry()
        h = Hypothesis(
            statement="test",
            prediction="test",
            falsification="fail",
            metric="win_rate",
            threshold=55.0,
            direction=">",
        )
        reg.register("exp_001", h)
        h2 = Hypothesis(
            statement="test2",
            prediction="test2",
            falsification="fail2",
            metric="win_rate",
            threshold=60.0,
            direction=">",
        )
        reg.register("exp_001", h2)
        rec = reg.get("exp_001")
        assert rec is not None
        assert rec.hypothesis.statement == "test2"

    def test_add_run(self) -> None:
        reg = ExperimentRegistry()
        h = Hypothesis(
            statement="test",
            prediction="test",
            falsification="fail",
            metric="win_rate",
            threshold=55.0,
            direction=">",
        )
        reg.register("exp_001", h)
        reg.add_run("exp_001", "run_001")
        reg.add_run("exp_001", "run_002")
        rec = reg.get("exp_001")
        assert rec is not None
        assert rec.run_ids == ["run_001", "run_002"]

    def test_add_run_missing_experiment(self) -> None:
        """添加不存在的实验 run 应静默忽略。"""
        reg = ExperimentRegistry()
        reg.add_run("nonexistent", "run_001")
        assert reg.get("nonexistent") is None

    def test_set_result_pass(self) -> None:
        reg = ExperimentRegistry()
        h = Hypothesis(
            statement="win_rate > 55",
            prediction="win_rate > 55",
            falsification="win_rate <= 55",
            metric="win_rate",
            threshold=55.0,
            direction=">",
        )
        reg.register("exp_001", h)
        passed, expl = reg.set_result("exp_001", {"win_rate": 60.0})
        assert passed is True
        rec = reg.get("exp_001")
        assert rec is not None
        assert rec.passed is True

    def test_set_result_fail(self) -> None:
        reg = ExperimentRegistry()
        h = Hypothesis(
            statement="win_rate > 55",
            prediction="win_rate > 55",
            falsification="win_rate <= 55",
            metric="win_rate",
            threshold=55.0,
            direction=">",
        )
        reg.register("exp_001", h)
        passed, expl = reg.set_result("exp_001", {"win_rate": 50.0})
        assert passed is False
        rec = reg.get("exp_001")
        assert rec is not None
        assert rec.passed is False

    def test_set_result_missing_experiment_raises(self) -> None:
        reg = ExperimentRegistry()
        with pytest.raises(KeyError):
            reg.set_result("nonexistent", {"win_rate": 60.0})

    def test_list_experiments(self) -> None:
        reg = ExperimentRegistry()
        h1 = Hypothesis(
            statement="a > 0",
            prediction="a > 0",
            falsification="a <= 0",
            metric="alpha",
            threshold=0.0,
            direction=">",
        )
        h2 = Hypothesis(
            statement="b > 0",
            prediction="b > 0",
            falsification="b <= 0",
            metric="beta",
            threshold=0.0,
            direction=">",
        )
        reg.register("exp_1", h1, exp_type=ExperimentType.ALPHA)
        reg.register("exp_2", h2, exp_type=ExperimentType.MODEL)
        all_exp = reg.list_experiments()
        assert len(all_exp) == 2

        alpha_only = reg.list_experiments(exp_type=ExperimentType.ALPHA)
        assert len(alpha_only) == 1
        assert alpha_only[0].experiment_id == "exp_1"

    def test_list_experiments_passed_filter(self) -> None:
        reg = ExperimentRegistry()
        h = Hypothesis(
            statement="w > 55",
            prediction="w > 55",
            falsification="w <= 55",
            metric="win_rate",
            threshold=55.0,
            direction=">",
        )
        reg.register("exp_1", h)
        reg.set_result("exp_1", {"win_rate": 60.0})
        reg.register("exp_2", h)
        reg.set_result("exp_2", {"win_rate": 50.0})

        passed = reg.list_experiments(only_passed=True)
        assert len(passed) == 1
        assert passed[0].experiment_id == "exp_1"

        failed = reg.list_experiments(only_passed=False)
        assert len(failed) == 1
        assert failed[0].experiment_id == "exp_2"

    def test_compare(self) -> None:
        reg = ExperimentRegistry()
        h = Hypothesis(
            statement="w > 55",
            prediction="w > 55",
            falsification="w <= 55",
            metric="win_rate",
            threshold=55.0,
            direction=">",
        )
        reg.register("exp_1", h)
        reg.set_result("exp_1", {"win_rate": 60.0, "sharpe": 1.5})
        reg.add_run("exp_1", "run_a")

        comp = reg.compare(["exp_1", "missing"])
        assert "exp_1" in comp
        assert comp["exp_1"]["win_rate"] == 60.0
        assert comp["exp_1"]["passed"] is True
        assert comp["exp_1"]["n_runs"] == 1
        assert "missing" not in comp

    def test_save_load_roundtrip(self, tmp_path: Path) -> None:
        reg = ExperimentRegistry()
        h = Hypothesis(
            statement="w > 55",
            prediction="w > 55",
            falsification="w <= 55",
            metric="win_rate",
            threshold=55.0,
            direction=">",
        )
        reg.register("exp_001", h, description="first experiment")
        reg.set_result("exp_001", {"win_rate": 62.0})
        reg.add_run("exp_001", "run_001")

        path = tmp_path / "experiments.json"
        reg.save(path)

        reg2 = ExperimentRegistry()
        reg2.load(path)

        rec2 = reg2.get("exp_001")
        assert rec2 is not None
        assert rec2.description == "first experiment"
        assert rec2.passed is True
        assert rec2.result_summary == {"win_rate": 62.0}
        assert rec2.run_ids == ["run_001"]

    def test_save_load_empty(self, tmp_path: Path) -> None:
        reg = ExperimentRegistry()
        path = tmp_path / "empty.json"
        reg.save(path)
        reg2 = ExperimentRegistry()
        reg2.load(path)
        assert len(reg2.list_experiments()) == 0

    def test_load_missing_file(self, tmp_path: Path) -> None:
        reg = ExperimentRegistry()
        reg.load(tmp_path / "nope.json")
        assert len(reg.list_experiments()) == 0

    def test_load_corrupt_json(self, tmp_path: Path) -> None:
        path = tmp_path / "corrupt.json"
        path.write_text("{invalid json")
        reg = ExperimentRegistry()
        with pytest.raises(Exception):  # json.JSONDecodeError
            reg.load(path)


# ═══════════════════════════════════════════════════════
#  register_alpha_experiment (factory helper)
# ═══════════════════════════════════════════════════════


class TestRegisterAlphaExperiment:
    def test_basic(self) -> None:
        reg = ExperimentRegistry()
        rec = register_alpha_experiment(
            reg,
            "exp_001",
            "Kronos UP signals outperform",
            prediction_threshold=55.0,
        )
        assert rec.experiment_id == "exp_001"
        assert rec.experiment_type is ExperimentType.ALPHA
        assert "win_rate > 55" in rec.hypothesis.prediction

    def test_custom_params(self) -> None:
        reg = ExperimentRegistry()
        rec = register_alpha_experiment(
            reg,
            "exp_002",
            "Alpha > 2%",
            prediction_metric="alpha",
            prediction_threshold=2.0,
            prediction_direction="<",
            description="Testing alpha threshold",
            config={"model": "kronos-v2"},
        )
        assert rec.hypothesis.metric == "alpha"
        assert rec.hypothesis.threshold == 2.0
        assert rec.hypothesis.direction == "<"
        assert rec.description == "Testing alpha threshold"
        assert rec.config == {"model": "kronos-v2"}

    def test_evaluate_pass(self) -> None:
        reg = ExperimentRegistry()
        register_alpha_experiment(reg, "exp_003", "win_rate > 50", prediction_threshold=50.0)
        passed, _ = reg.set_result("exp_003", {"win_rate": 55.0})
        assert passed is True

    def test_evaluate_fail(self) -> None:
        reg = ExperimentRegistry()
        register_alpha_experiment(reg, "exp_004", "win_rate > 50")
        passed, _ = reg.set_result("exp_004", {"win_rate": 45.0})
        assert passed is False
