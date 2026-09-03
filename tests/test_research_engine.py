"""tests for V0.2 Research Engine modules:
· data_snapshot        — Point-in-Time data integrity
· unified_decision     — EV computation + conflict detection
· eval_walkforward     — rolling window evaluation
· experiment_registry  — hypothesis tracking.
"""

import pandas as pd
import pytest

# ═══════════════════════════════════════════════════════
#  DataSnapshot
# ═══════════════════════════════════════════════════════


class TestDataSnapshot:
    def test_basic_construction(self) -> None:
        from trade_krono_cli.data_snapshot import DataSnapshot, DataSourceSnapshot

        snap = DataSnapshot(
            cut_date="2024-06-30",
            sources=(
                DataSourceSnapshot(
                    source="baostock",
                    cut_date="2024-06-30",
                    latest_date="2024-06-28",
                    record_count=500,
                    data_hash="abc",
                ),
            ),
            description="test",
        )
        assert snap.snapshot_id  # non-empty
        assert snap.effective_cut_date() == "2024-06-28"
        assert snap.contains_future_data("sh.600519", "2024-06-29") is True
        assert snap.contains_future_data("sh.600519", "2024-06-25") is False

    def test_empty_sources(self) -> None:
        from trade_krono_cli.data_snapshot import DataSnapshot

        snap = DataSnapshot(cut_date="2024-06-30")
        assert snap.effective_cut_date() == "2024-06-30"
        assert snap.contains_future_data("x", "2024-07-01") is False

    def test_roundtrip_serialization(self) -> None:
        from trade_krono_cli.data_snapshot import DataSnapshot, DataSourceSnapshot

        snap = DataSnapshot(
            cut_date="2024-06-30",
            sources=(
                DataSourceSnapshot(
                    source="akshare",
                    cut_date="2024-06-30",
                    latest_date="2024-06-20",
                    record_count=200,
                ),
            ),
            description="roundtrip",
        )
        restored = DataSnapshot.from_dict(snap.to_dict())
        assert restored.snapshot_id == snap.snapshot_id
        assert restored.cut_date == "2024-06-30"
        assert len(restored.sources) == 1
        assert restored.sources[0].source == "akshare"

    def test_frozen_immutable(self) -> None:
        from trade_krono_cli.data_snapshot import DataSnapshot

        snap = DataSnapshot(cut_date="2024-01-01")
        with pytest.raises(AttributeError):
            snap.cut_date = "2024-12-31"  # frozen


class TestFilterKlineToCutDate:
    def test_filters_correctly(self) -> None:
        from trade_krono_cli.data_snapshot import filter_kline_to_cut_date

        df = pd.DataFrame(
            {
                "timestamps": ["2024-06-20", "2024-06-25", "2024-06-28", "2024-07-01"],
                "close": [100, 101, 102, 103],
            },
        )
        result = filter_kline_to_cut_date(df, "2024-06-28")
        assert len(result) == 3
        assert result.iloc[-1]["timestamps"] == "2024-06-28"

    def test_none_input(self) -> None:
        from trade_krono_cli.data_snapshot import filter_kline_to_cut_date

        assert filter_kline_to_cut_date(None, "2024-06-28") is None

    def test_empty_df(self) -> None:
        from trade_krono_cli.data_snapshot import filter_kline_to_cut_date

        df = pd.DataFrame({"timestamps": [], "close": []})
        result = filter_kline_to_cut_date(df, "2024-06-28")
        assert len(result) == 0


# ═══════════════════════════════════════════════════════
#  UnifiedInvestmentDecision
# ═══════════════════════════════════════════════════════


class TestUnifiedInvestmentDecision:
    def test_build_with_kronos_only(self) -> None:
        from trade_krono_cli.unified_decision import build_unified_decision

        dec = build_unified_decision(
            ticker="sh.600519",
            eval_date="2024-06-30",
            kronos_direction="UP",
            kronos_expected_return=3.0,
            distribution={
                "p10": 1700,
                "p25": 1750,
                "p50": 1800,
                "p75": 1850,
                "p90": 1900,
                "predicted_close_final": 1800,
            },
        )
        assert dec.final_signal.value == "BUY"
        assert dec.kronos_direction == "UP"
        assert dec.expected_value is not None
        assert dec.prob_win is not None

    def test_build_with_ta_and_conflict(self) -> None:
        from trade_krono_cli.ta_decision import InvestmentDecision, Signal
        from trade_krono_cli.unified_decision import build_unified_decision

        ta = InvestmentDecision(signal=Signal.BUY, confidence=80.0)
        dec = build_unified_decision(
            ticker="sh.600519",
            eval_date="2024-06-30",
            ta_decision=ta,
            kronos_direction="DOWN",
            kronos_expected_return=-2.0,
            distribution={
                "p10": 1700,
                "p25": 1750,
                "p50": 1800,
                "p75": 1850,
                "p90": 1900,
                "predicted_close_final": 1800,
            },
        )
        assert dec.conflict == "ta_vs_kronos"
        assert dec.final_signal is not None

    def test_build_all_three_sources_agree(self) -> None:
        from trade_krono_cli.ta_decision import InvestmentDecision
        from trade_krono_cli.unified_decision import Signal, build_unified_decision

        ta = InvestmentDecision(signal=Signal.BUY, confidence=80.0)
        dec = build_unified_decision(
            ticker="sh.600519",
            eval_date="2024-06-30",
            ta_decision=ta,
            kronos_direction="UP",
            kronos_expected_return=3.0,
            committee_rec=Signal.BUY,
            committee_confidence=75.0,
            distribution={
                "p10": 1700,
                "p25": 1750,
                "p50": 1800,
                "p75": 1850,
                "p90": 1900,
                "predicted_close_final": 1800,
            },
        )
        assert dec.conflict == "none"
        assert dec.final_signal.value == "BUY"

    def test_roundtrip_serialization(self) -> None:
        from trade_krono_cli.unified_decision import build_unified_decision

        dec = build_unified_decision(
            ticker="sh.600519",
            eval_date="2024-06-30",
            kronos_direction="UP",
            kronos_expected_return=2.5,
            distribution={
                "p10": 1700,
                "p25": 1750,
                "p50": 1800,
                "p75": 1850,
                "p90": 1900,
                "predicted_close_final": 1800,
            },
        )
        d = dec.to_dict()
        restored = type(dec).from_dict(d)
        assert restored.ticker == dec.ticker
        assert restored.expected_value == dec.expected_value
        assert restored.final_signal == dec.final_signal

    def test_compute_expected_value_negative(self) -> None:
        from trade_krono_cli.unified_decision import build_unified_decision

        dec = build_unified_decision(
            ticker="sh.600519",
            eval_date="2024-06-30",
            kronos_direction="DOWN",
            kronos_expected_return=-4.0,
            distribution={
                "p10": 1600,
                "p25": 1700,
                "p50": 1750,
                "p75": 1800,
                "p90": 1850,
                "predicted_close_final": 1800,
            },
        )
        assert dec.expected_value is not None
        assert dec.expected_value < 0  # negative EV

    def test_to_ta_decision_compat(self) -> None:
        from trade_krono_cli.unified_decision import build_unified_decision

        dec = build_unified_decision(
            ticker="sh.600519",
            eval_date="2024-06-30",
            kronos_direction="UP",
            kronos_expected_return=2.0,
            distribution={
                "p10": 1700,
                "p25": 1750,
                "p50": 1800,
                "p75": 1850,
                "p90": 1900,
                "predicted_close_final": 1800,
            },
        )
        old = dec.to_ta_decision()
        assert old.signal.value == "BUY"
        assert old.expected_return == 2.0


# ═══════════════════════════════════════════════════════
#  WalkForwardEngine
# ═══════════════════════════════════════════════════════


class TestWalkForwardEngine:
    def test_quick_run_basic(self) -> None:
        from trade_krono_cli.eval_walkforward import run_walk_forward_quick

        def predict(ticker, date):
            return {
                "direction": "UP",
                "expected_change_pct": 2.0,
                "p10": 100,
                "p25": 102,
                "p50": 104,
                "p75": 106,
                "p90": 108,
            }

        def fetch_actual(ticker, date, horizon) -> float:
            return 1.5 if horizon == 5 else 2.0

        result = run_walk_forward_quick(
            "sh.600519",
            eval_dates=["2024-01-15", "2024-02-15"],
            predict_fn=predict,
            fetch_actual_fn=fetch_actual,
            horizons=(5, 10),
        )
        assert result.run_id.startswith("wf_")
        assert result.total_windows == 2
        assert len(result.records) == 4  # 2 dates × 2 horizons
        assert result.win_rate == 100.0  # all UP predicted, all positive actual
        assert result.avg_return == pytest.approx(1.75, abs=0.01)

    def test_quick_run_with_mixed_results(self) -> None:
        from trade_krono_cli.eval_walkforward import run_walk_forward_quick

        outcomes = [2.0, -1.0, 2.0, -1.0, 2.0, -1.0]  # 3 dates × 2 horizons
        idx = [0]

        def predict(ticker, date):
            return {"direction": "UP", "expected_change_pct": 1.0}

        def fetch_actual(ticker, date, horizon):
            val = outcomes[idx[0] % len(outcomes)]
            idx[0] += 1
            return val

        result = run_walk_forward_quick(
            "sh.600519",
            eval_dates=["2024-01-15", "2024-02-15", "2024-03-15"],
            predict_fn=predict,
            fetch_actual_fn=fetch_actual,
            horizons=(5, 10),
        )
        assert len(result.records) == 6
        assert result.win_rate == pytest.approx(50.0, abs=0.1)

    def test_empty_eval_dates(self) -> None:
        from trade_krono_cli.eval_walkforward import run_walk_forward_quick

        result = run_walk_forward_quick(
            "sh.600519",
            eval_dates=[],
            predict_fn=lambda t, d: None,
            fetch_actual_fn=lambda t, d, h: None,
        )
        assert result.total_windows == 0
        assert result.records == []

    def test_predict_returns_none_skips(self) -> None:
        from trade_krono_cli.eval_walkforward import run_walk_forward_quick

        def predict(ticker, date) -> None:
            return None  # prediction fails

        result = run_walk_forward_quick(
            "sh.600519",
            eval_dates=["2024-01-15"],
            predict_fn=predict,
            fetch_actual_fn=lambda t, d, h: 1.0,
        )
        assert result.total_windows == 1
        assert len(result.records) == 0


# ═══════════════════════════════════════════════════════
#  ExperimentRegistry
# ═══════════════════════════════════════════════════════


class TestExperimentRegistry:
    def test_register_and_evaluate_pass(self) -> None:
        from trade_krono_cli.experiment_registry import (
            ExperimentRegistry,
            register_alpha_experiment,
        )

        reg = ExperimentRegistry()
        exp = register_alpha_experiment(
            reg,
            "exp_001",
            "Win rate > 55%",
            prediction_threshold=55.0,
        )
        assert exp.experiment_id == "exp_001"
        passed, expl = reg.set_result("exp_001", {"win_rate": 62.5})
        assert passed is True
        assert "✅" in expl

    def test_evaluate_fail(self) -> None:
        from trade_krono_cli.experiment_registry import (
            ExperimentRegistry,
            register_alpha_experiment,
        )

        reg = ExperimentRegistry()
        register_alpha_experiment(
            reg,
            "exp_002",
            "Sharpe > 2.0",
            prediction_metric="sharpe",
            prediction_threshold=2.0,
        )
        passed, _ = reg.set_result("exp_002", {"sharpe": 1.3})
        assert passed is False

    def test_compare_multiple_experiments(self) -> None:
        from trade_krono_cli.experiment_registry import (
            ExperimentRegistry,
            register_alpha_experiment,
        )

        reg = ExperimentRegistry()
        register_alpha_experiment(reg, "a", "H1", prediction_threshold=50.0)
        register_alpha_experiment(reg, "b", "H2", prediction_threshold=60.0)
        reg.set_result("a", {"win_rate": 55.0})
        reg.set_result("b", {"win_rate": 65.0})
        cmp = reg.compare(["a", "b"])
        assert cmp["a"]["passed"] is True
        assert cmp["b"]["passed"] is True

    def test_save_and_load(self, tmp_path) -> None:
        from trade_krono_cli.experiment_registry import (
            ExperimentRegistry,
            register_alpha_experiment,
        )

        reg = ExperimentRegistry()
        register_alpha_experiment(reg, "exp_x", "test hyp")
        reg.set_result("exp_x", {"win_rate": 70.0})
        path = tmp_path / "experiments.json"
        reg.save(path)
        reg2 = ExperimentRegistry()
        reg2.load(path)
        assert reg2.get("exp_x") is not None
        assert reg2.get("exp_x").result_summary["win_rate"] == 70.0

    def test_list_filter_by_type(self) -> None:
        from trade_krono_cli.experiment_registry import (
            ExperimentRegistry,
            ExperimentType,
            Hypothesis,
        )

        reg = ExperimentRegistry()
        reg.register("e1", Hypothesis("h1", "p1", "f1"), ExperimentType.ALPHA)
        reg.register("e2", Hypothesis("h2", "p2", "f2"), ExperimentType.MODEL)
        alpha = reg.list_experiments(exp_type=ExperimentType.ALPHA)
        assert len(alpha) == 1
        assert alpha[0].experiment_id == "e1"
        models = reg.list_experiments(exp_type=ExperimentType.MODEL)
        assert len(models) == 1
        assert models[0].experiment_id == "e2"

    def test_hypothesis_check_edge_cases(self) -> None:
        from trade_krono_cli.experiment_registry import Hypothesis

        hyp = Hypothesis("h", "p", "f", metric="x", threshold=10.0, direction="<")
        passed, _ = hyp.check(5.0)
        assert passed is True
        passed, _ = hyp.check(15.0)
        assert passed is False

    def test_evaluate_missing_metric(self) -> None:
        from trade_krono_cli.experiment_registry import ExperimentRegistry, Hypothesis

        reg = ExperimentRegistry()
        reg.register("e1", Hypothesis("h", "p", "f", metric="win_rate", threshold=50.0))
        passed, expl = reg.set_result("e1", {"sharpe": 1.5})  # missing win_rate
        assert passed is False
        assert "win_rate" in expl


# ═══════════════════════════════════════════════════════
#  Integration: WalkForward → ExperimentRegistry
# ═══════════════════════════════════════════════════════


class TestWalkForwardExperimentIntegration:
    def test_end_to_end(self) -> None:
        """WalkForward 结果自动填入 Experiment 假设验证。"""
        from trade_krono_cli.eval_walkforward import run_walk_forward_quick
        from trade_krono_cli.experiment_registry import (
            ExperimentRegistry,
            register_alpha_experiment,
        )

        reg = ExperimentRegistry()
        register_alpha_experiment(
            reg,
            "wf_exp_001",
            "Kronos UP 信号在 2024 Q2 的胜率 > 50%",
            prediction_threshold=50.0,
        )

        def predict(ticker, date):
            return {
                "direction": "UP",
                "expected_change_pct": 2.0,
                "p10": 100,
                "p25": 102,
                "p50": 104,
                "p75": 106,
                "p90": 108,
            }

        def fetch_actual(ticker, date, horizon) -> float:
            return 1.5

        result = run_walk_forward_quick(
            "sh.600519",
            eval_dates=["2024-01-15", "2024-02-15", "2024-03-15"],
            predict_fn=predict,
            fetch_actual_fn=fetch_actual,
            horizons=(5, 10),
        )
        # 将 walk-forward 结果关联到实验
        reg.add_run("wf_exp_001", result.run_id)
        reg.set_result(
            "wf_exp_001",
            {
                "win_rate": result.win_rate,
                "sharpe": result.sharpe_annual,
                "avg_return": result.avg_return,
            },
        )
        exp = reg.get("wf_exp_001")
        assert exp is not None
        assert exp.passed is True
        assert result.run_id in exp.run_ids
