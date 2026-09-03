"""Tests for trade_krono_cli.eval_walkforward — WalkForwardEngine.

覆盖 WalkForwardConfig、WalkForwardResult、WalkForwardEngine.run() 的关键分支。
"""

from __future__ import annotations

from unittest.mock import MagicMock

from trade_krono_cli.eval_walkforward import (
    WalkForwardConfig,
    WalkForwardEngine,
    WalkForwardResult,
)

# ═══════════════════════════════════════════════════════
#  WalkForwardConfig
# ═══════════════════════════════════════════════════════


class TestWalkForwardConfig:
    def test_defaults(self) -> None:
        cfg = WalkForwardConfig()
        assert cfg.lookback_days == 252
        assert cfg.step_days == 20
        assert cfg.horizons == (5, 10, 20, 30)
        assert cfg.min_train_samples == 60

    def test_custom(self) -> None:
        cfg = WalkForwardConfig(lookback_days=60, step_days=10, horizons=(10, 30), min_train_samples=20)
        assert cfg.lookback_days == 60
        assert cfg.horizons == (10, 30)


# ═══════════════════════════════════════════════════════
#  WalkForwardResult
# ═══════════════════════════════════════════════════════


class TestWalkForwardResult:
    def test_empty_result(self) -> None:
        r = WalkForwardResult(run_id="r1", config=WalkForwardConfig())
        assert r.records == []
        assert r.summary is None  # summary is only set after run()

    def test_with_records(self) -> None:
        rec = MagicMock()
        rec.ticker = "sh.600519"
        rec.direction = "UP"
        rec.expected_change_pct = 2.0
        rec.actual_return_pct = 1.5
        rec.horizon = 30
        rec.eval_date = "2026-08-11"

        r = WalkForwardResult(run_id="run_001", config=WalkForwardConfig(), records=[rec])
        assert len(r.records) == 1
        assert r.run_id == "run_001"
        assert r.summary is None  # not computed yet


# ═══════════════════════════════════════════════════════
#  WalkForwardEngine
# ═══════════════════════════════════════════════════════


class TestWalkForwardEngineRun:
    def _make_snapshot(self, **kwargs):
        """Create a mock DataSnapshot with configurable effective_cut_date."""
        snap = MagicMock()
        snap.contains_future_data = MagicMock(return_value=False)
        cut = kwargs.get("cut_date", "2026-08-01")
        snap.effective_cut_date.return_value = cut
        return snap

    def test_basic_run(self) -> None:
        """基本 run：predict_fn 返回预测，fetch_actual_fn 返回实际收益。"""
        cfg = WalkForwardConfig(
            test_start_date="",  # 通过 data_snapshot 提供
            test_end_date="2026-08-15",
            step_days=7,
            horizons=(30,),
        )
        engine = WalkForwardEngine(config=cfg)
        predict_fn = MagicMock(return_value={
            "direction": "UP",
            "expected_change_pct": 2.0,
            "p10": 95.0, "p90": 110.0,
        })
        fetch_actual_fn = MagicMock(return_value=1.5)
        snapshot = self._make_snapshot(cut_date="2026-08-01")

        result = engine.run(
            ticker="sh.600519",
            predict_fn=predict_fn,
            fetch_actual_fn=fetch_actual_fn,
            data_snapshot=snapshot,
        )

        assert isinstance(result, WalkForwardResult)
        assert predict_fn.call_count >= 1
        assert fetch_actual_fn.call_count >= 1
        assert len(result.records) >= 1

    def test_predict_returns_none_skips(self) -> None:
        """predict_fn 返回 None 时应跳过该日期。"""
        cfg = WalkForwardConfig(
            test_start_date="",
            test_end_date="2026-08-15",
            step_days=7,
            horizons=(30,),
        )
        engine = WalkForwardEngine(config=cfg)
        predict_fn = MagicMock(return_value=None)
        fetch_actual_fn = MagicMock(return_value=1.0)
        snapshot = self._make_snapshot(cut_date="2026-08-01")

        result = engine.run(
            ticker="sh.600519",
            predict_fn=predict_fn,
            fetch_actual_fn=fetch_actual_fn,
            data_snapshot=snapshot,
        )
        assert len(result.records) == 0

    def test_fetch_actual_returns_none_skips(self) -> None:
        """fetch_actual_fn 返回 None 时应跳过该日期。"""
        cfg = WalkForwardConfig(
            test_start_date="",
            test_end_date="2026-08-15",
            step_days=7,
            horizons=(30,),
        )
        engine = WalkForwardEngine(config=cfg)
        predict_fn = MagicMock(return_value={"direction": "UP", "expected_change_pct": 2.0})
        fetch_actual_fn = MagicMock(return_value=None)
        snapshot = self._make_snapshot(cut_date="2026-08-01")

        result = engine.run(
            ticker="sh.600519",
            predict_fn=predict_fn,
            fetch_actual_fn=fetch_actual_fn,
            data_snapshot=snapshot,
        )
        assert len(result.records) == 0

    def test_empty_eval_dates(self) -> None:
        """test_start == test_end → 无生成日期 → 返回空结果。"""
        cfg = WalkForwardConfig(
            test_start_date="",
            test_end_date="2026-08-11",
            step_days=7,
            horizons=(30,),
        )
        engine = WalkForwardEngine(config=cfg)
        snapshot = self._make_snapshot(cut_date="2026-08-11")
        result = engine.run(
            ticker="sh.600519",
            predict_fn=MagicMock(),
            fetch_actual_fn=MagicMock(),
            data_snapshot=snapshot,
        )
        assert len(result.records) == 0
        assert result.total_windows == 0

    def test_data_snapshot_future_skip(self) -> None:
        """data_snapshot 中包含的未来日期应被跳过。"""
        cfg = WalkForwardConfig(
            test_start_date="",
            test_end_date="2026-08-15",
            step_days=7,
            horizons=(30,),
        )
        snapshot = MagicMock()
        snapshot.contains_future_data = MagicMock(return_value=True)
        snapshot.effective_cut_date.return_value = "2026-08-01"

        engine = WalkForwardEngine(config=cfg)
        predict_fn = MagicMock(return_value={"direction": "UP", "expected_change_pct": 2.0})
        fetch_actual_fn = MagicMock(return_value=1.0)

        result = engine.run(
            ticker="sh.600519",
            predict_fn=predict_fn,
            fetch_actual_fn=fetch_actual_fn,
            data_snapshot=snapshot,
        )
        assert isinstance(result, WalkForwardResult)
        assert len(result.records) == 0

    def test_min_train_samples_filtering(self) -> None:
        """min_train_samples 不足时应跳过该窗口。"""
        cfg = WalkForwardConfig(
            test_start_date="",
            test_end_date="2026-08-15",
            step_days=7,
            horizons=(30,),
            min_train_samples=100,
        )
        engine = WalkForwardEngine(config=cfg)
        predict_fn = MagicMock(return_value={"direction": "UP", "expected_change_pct": 2.0})
        fetch_actual_fn = MagicMock(return_value=1.0)
        train_fn = MagicMock(return_value=None)  # 返回 None → 训练数据不足
        snapshot = self._make_snapshot(cut_date="2026-08-01")

        result = engine.run(
            ticker="sh.600519",
            predict_fn=predict_fn,
            fetch_actual_fn=fetch_actual_fn,
            train_data_fn=train_fn,
            data_snapshot=snapshot,
        )
        assert len(result.records) == 0


class TestWalkForwardEngineSummary:
    def _make_snapshot(self, **kwargs):
        """Create a mock DataSnapshot with configurable effective_cut_date."""
        snap = MagicMock()
        snap.contains_future_data = MagicMock(return_value=False)
        cut = kwargs.get("cut_date", "2026-08-01")
        snap.effective_cut_date.return_value = cut
        return snap

    def test_summary_computed(self) -> None:
        cfg = WalkForwardConfig(
            test_start_date="",
            test_end_date="2026-08-15",
            step_days=7,
            horizons=(30,),
        )
        engine = WalkForwardEngine(config=cfg)
        predict_fn = MagicMock(return_value={"direction": "UP", "expected_change_pct": 3.0})
        fetch_actual_fn = MagicMock(side_effect=[2.0, -1.0, 4.0])
        snapshot = self._make_snapshot(cut_date="2026-08-01")

        result = engine.run(
            ticker="sh.600519",
            predict_fn=predict_fn,
            fetch_actual_fn=fetch_actual_fn,
            data_snapshot=snapshot,
        )
        assert result.summary is not None
        assert len(result.records) > 0

    def test_summary_with_mixed_results(self) -> None:
        cfg = WalkForwardConfig(
            test_start_date="",
            test_end_date="2026-08-15",
            step_days=7,
            horizons=(10, 30),
        )
        engine = WalkForwardEngine(config=cfg)
        predict_fn = MagicMock(return_value={"direction": "UP", "expected_change_pct": 2.0})
        fetch_actual_fn = MagicMock(side_effect=[5.0, -3.0, 0.0, 2.0, -1.0])
        snapshot = self._make_snapshot(cut_date="2026-08-01")

        result = engine.run(
            ticker="sh.600519",
            predict_fn=predict_fn,
            fetch_actual_fn=fetch_actual_fn,
            data_snapshot=snapshot,
        )
        assert result.summary is not None
        assert len(result.records) > 0


class TestWalkForwardQuick:
    def test_quick_run(self) -> None:
        """run_walk_forward_quick 便捷函数应正常工作。"""
        from trade_krono_cli.eval_walkforward import run_walk_forward_quick

        predict_fn = MagicMock(return_value={"direction": "UP", "expected_change_pct": 2.0})
        fetch_actual_fn = MagicMock(return_value=1.5)

        result = run_walk_forward_quick(
            ticker="sh.600519",
            eval_dates=["2026-08-11"],
            predict_fn=predict_fn,
            fetch_actual_fn=fetch_actual_fn,
        )
        assert isinstance(result, WalkForwardResult)
        # Default horizons=(5,10,20,30), 1 eval_date → 4 records (one per horizon)
        assert len(result.records) == 4
        assert result.records[0].ticker == "sh.600519"
        assert result.records[0].pred_direction == "UP"
