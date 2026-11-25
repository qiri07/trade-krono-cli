"""trade_krono_cli.domain.experiment 单元测试。

覆盖 Hypothesis 与 Experiment 数据对象的 check / to_dict / from_dict /
evaluate 以及 build_alpha_experiment 工厂函数。
"""

from __future__ import annotations

from trade_krono_cli.domain.experiment import Experiment, Hypothesis, build_alpha_experiment
from trade_krono_cli.domain.types import ExperimentType


class TestHypothesis:
    """Hypothesis 验证与序列化测试。"""

    def test_check_greater_than_pass(self) -> None:
        hyp = Hypothesis(
            statement="胜率超过 55%",
            prediction="win_rate > 55",
            falsification="win_rate <= 55",
            metric="win_rate",
            threshold=55.0,
            direction=">",
        )
        passed, expl = hyp.check(60.0)
        assert passed is True
        assert "✅ 通过" in expl

    def test_check_greater_than_fail(self) -> None:
        hyp = Hypothesis(
            statement="胜率超过 55%",
            prediction="win_rate > 55",
            falsification="win_rate <= 55",
            metric="win_rate",
            threshold=55.0,
            direction=">",
        )
        passed, expl = hyp.check(50.0)
        assert passed is False
        assert "❌ 未通过" in expl

    def test_check_less_than(self) -> None:
        hyp = Hypothesis(
            statement="回撤小于 10%",
            prediction="drawdown < 10",
            falsification="drawdown >= 10",
            metric="drawdown",
            threshold=10.0,
            direction="<",
        )
        assert hyp.check(5.0)[0] is True
        assert hyp.check(15.0)[0] is False

    def test_check_equal(self) -> None:
        hyp = Hypothesis(
            statement="等于 1",
            prediction="x == 1",
            falsification="x != 1",
            metric="x",
            threshold=1.0,
            direction="==",
        )
        assert hyp.check(1.0)[0] is True
        assert hyp.check(1.005)[0] is True
        assert hyp.check(2.0)[0] is False

    def test_check_unknown_direction(self) -> None:
        hyp = Hypothesis(
            statement="未知方向",
            prediction="x",
            falsification="not x",
            metric="x",
            threshold=1.0,
            direction="invalid",
        )
        assert hyp.check(1.0)[0] is False

    def test_to_dict_round_trip(self) -> None:
        hyp = Hypothesis(
            statement="s",
            prediction="p",
            falsification="f",
            metric="m",
            threshold=1.5,
            direction=">",
        )
        data = hyp.to_dict()
        assert data == {
            "statement": "s",
            "prediction": "p",
            "falsification": "f",
            "metric": "m",
            "threshold": 1.5,
            "direction": ">",
        }
        restored = Hypothesis.from_dict(data)
        assert restored == hyp

    def test_from_dict_defaults(self) -> None:
        hyp = Hypothesis.from_dict(
            {"statement": "s", "prediction": "p", "falsification": "f"}
        )
        assert hyp.metric == "win_rate"
        assert hyp.threshold == 0.0
        assert hyp.direction == ">"
        assert hyp.statement == "s"


class TestExperiment:
    """Experiment 序列化与 evaluate 测试。"""

    def _make_hypothesis(self) -> Hypothesis:
        return Hypothesis(
            statement="Alpha 胜率 > 55%",
            prediction="win_rate > 55",
            falsification="win_rate <= 55",
            metric="win_rate",
            threshold=55.0,
            direction=">",
        )

    def test_full_id_is_sha256(self) -> None:
        hyp = self._make_hypothesis()
        exp = Experiment(
            experiment_id="exp_001",
            experiment_type=ExperimentType.ALPHA,
            hypothesis=hyp,
            description="desc",
            created_at="2026-01-01T00:00:00+00:00",
        )
        fid = exp.full_id
        assert len(fid) == 32
        assert all(c in "0123456789abcdef" for c in fid)
        # 相同输入产生相同 ID
        exp2 = Experiment(
            experiment_id="exp_001",
            experiment_type=ExperimentType.ALPHA,
            hypothesis=hyp,
            description="desc",
            created_at="2026-01-01T00:00:00+00:00",
        )
        assert exp2.full_id == fid

    def test_evaluate_pass(self) -> None:
        hyp = self._make_hypothesis()
        exp = Experiment(
            experiment_id="exp_001",
            experiment_type=ExperimentType.ALPHA,
            hypothesis=hyp,
            result_summary={"win_rate": 60.0},
        )
        passed, expl = exp.evaluate()
        assert passed is True
        assert "60.0" in expl

    def test_evaluate_fail(self) -> None:
        hyp = self._make_hypothesis()
        exp = Experiment(
            experiment_id="exp_001",
            experiment_type=ExperimentType.ALPHA,
            hypothesis=hyp,
            result_summary={"win_rate": 40.0},
        )
        passed, _ = exp.evaluate()
        assert passed is False

    def test_evaluate_missing_metric(self) -> None:
        hyp = self._make_hypothesis()
        exp = Experiment(
            experiment_id="exp_001",
            experiment_type=ExperimentType.ALPHA,
            hypothesis=hyp,
            result_summary={"other": 1.0},
        )
        passed, expl = exp.evaluate()
        assert passed is False
        assert "缺少指标" in expl

    def test_to_dict_from_dict_round_trip(self) -> None:
        hyp = self._make_hypothesis()
        exp = Experiment(
            experiment_id="exp_001",
            experiment_type=ExperimentType.MODEL,
            hypothesis=hyp,
            description="desc",
            config={"lr": 0.01},
            data_snapshot_id="snap_1",
            run_ids=["run_1", "run_2"],
            result_summary={"win_rate": 60.0},
            passed=True,
            notes="note",
            created_at="2026-01-01T00:00:00+00:00",
        )
        data = exp.to_dict()
        restored = Experiment.from_dict(data)
        assert restored.experiment_id == exp.experiment_id
        assert restored.experiment_type == exp.experiment_type
        assert restored.hypothesis == exp.hypothesis
        assert restored.passed == exp.passed
        assert restored.run_ids == exp.run_ids
        assert restored.result_summary == exp.result_summary

    def test_from_dict_defaults(self) -> None:
        exp = Experiment.from_dict(
            {"experiment_id": "e1", "experiment_type": "alpha"}
        )
        assert exp.experiment_id == "e1"
        assert exp.experiment_type == ExperimentType.ALPHA
        assert exp.hypothesis.statement == ""
        assert exp.passed is None


class TestBuildAlphaExperiment:
    """build_alpha_experiment 工厂函数测试。"""

    def test_build_alpha_experiment_defaults(self) -> None:
        exp = build_alpha_experiment(
            "exp_001",
            "Kronos UP 胜率超过 55%",
            prediction_threshold=55.0,
        )
        assert exp.experiment_id == "exp_001"
        assert exp.experiment_type == ExperimentType.ALPHA
        assert exp.hypothesis.statement == "Kronos UP 胜率超过 55%"
        assert exp.hypothesis.metric == "win_rate"
        assert exp.hypothesis.threshold == 55.0
        assert exp.hypothesis.direction == ">"
        assert exp.config == {}
        assert exp.passed is None

    def test_build_alpha_experiment_with_overrides(self) -> None:
        exp = build_alpha_experiment(
            "exp_002",
            "MA 策略胜率",
            prediction_metric="sharpe",
            prediction_threshold=1.5,
            description="d",
            config={"a": 1},
            data_snapshot_id="snap",
        )
        assert exp.hypothesis.metric == "sharpe"
        assert exp.hypothesis.threshold == 1.5
        assert exp.description == "d"
        assert exp.config == {"a": 1}
        assert exp.data_snapshot_id == "snap"
