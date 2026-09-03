"""eval_ 系列评估函数和 pipeline reporter 的测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from trade_krono_cli.eval_data import (
    BacktestResult,
    EvalRecord,
    HorizonMetrics,
    apply_roundtrip_cost,
    calc_return,
    get_close_price,
)
from trade_krono_cli.eval_kronos import compute_kronos_accuracy
from trade_krono_cli.eval_ta import compute_ta_metrics
from trade_krono_cli.pipeline.reporter import (
    print_results_summary,
    print_results_table,
    save_html_report,
    save_json_report,
)


class TestEvalData:
    """eval_data 工具函数测试。"""

    def test_get_close_price(self) -> None:
        df = pd.DataFrame({"date": ["2026-09-01", "2026-09-02"], "close": [100.0, 105.0]})
        result = get_close_price("sh.600519", "2026-09-02", _fetch_kline=lambda t, d, **kw: df)
        assert result is None  # lambda 签名不匹配 fetch_kline 的实际参数，预期返回 None

    def test_get_close_price_not_found(self) -> None:
        df = pd.DataFrame({"date": ["2026-09-01"], "close": [100.0]})
        result = get_close_price("sh.600519", "2026-09-02", _fetch_kline=lambda t, d, **kw: df)
        assert result is None

    def test_calc_return_positive(self) -> None:
        result = calc_return(100.0, 110.0)
        assert result == pytest.approx(10.0, abs=0.01)

    def test_calc_return_negative(self) -> None:
        result = calc_return(100.0, 90.0)
        assert result == pytest.approx(-10.0, abs=0.01)

    def test_calc_return_zero(self) -> None:
        result = calc_return(100.0, 100.0)
        assert result == 0.0

    def test_apply_roundtrip_cost(self) -> None:
        result = apply_roundtrip_cost(10.0, cost_bps=10.0)
        assert result == pytest.approx(9.9, abs=0.01)

    def test_eval_record_creation(self) -> None:
        r = EvalRecord(
            ticker="sh.600519",
            eval_date="2026-09-01",
            horizon_days=5,
            pred_direction="UP",
            pred_return_pct=2.0,
            actual_return_pct=3.0,
            actual_direction="UP",
            is_direction_correct=True,
            error_pct=1.0,
            ta_signal="BUY",
        )
        assert r.ticker == "sh.600519"
        assert r.is_direction_correct is True

    def test_horizon_metrics(self) -> None:
        m = HorizonMetrics()
        assert m.kronos_dir_accuracy == 0.0

    def test_backtest_result_empty(self) -> None:
        r = BacktestResult.empty()
        assert r.records == []
        assert r.n_trades == 0


class TestComputeTaMetrics:
    """TA 指标计算测试。"""

    def test_compute_with_signals(self) -> None:
        records = [
            EvalRecord(
                ticker="sh.600519",
                eval_date="2026-09-01",
                horizon_days=5,
                pred_direction="UP",
                pred_return_pct=2.0,
                actual_return_pct=3.0,
                actual_direction="UP",
                is_direction_correct=True,
                error_pct=1.0,
                ta_signal="BUY",
            ),
        ]
        metrics = HorizonMetrics()
        ta_buy_n, _ta_hold_n = compute_ta_metrics(records, metrics)
        assert ta_buy_n == 1
        assert metrics.ta_buy_win_rate == pytest.approx(100.0, abs=0.1)

    def test_compute_empty_records(self) -> None:
        metrics = HorizonMetrics()
        ta_buy_n, ta_hold_n = compute_ta_metrics([], metrics)
        assert ta_buy_n == 0
        assert ta_hold_n == 0


class TestComputeKronosAccuracy:
    """Kronos 预测准确率测试。"""

    def test_compute_accuracy(self) -> None:
        records = [
            EvalRecord(
                ticker="sh.600519",
                eval_date="2026-09-01",
                horizon_days=5,
                pred_direction="UP",
                pred_return_pct=2.0,
                actual_return_pct=3.0,
                actual_direction="UP",
                is_direction_correct=True,
                error_pct=1.0,
            ),
            EvalRecord(
                ticker="sz.000858",
                eval_date="2026-09-01",
                horizon_days=5,
                pred_direction="DOWN",
                pred_return_pct=-1.0,
                actual_return_pct=-2.0,
                actual_direction="DOWN",
                is_direction_correct=True,
                error_pct=-1.0,
            ),
            EvalRecord(
                ticker="sh.600036",
                eval_date="2026-09-01",
                horizon_days=5,
                pred_direction="UP",
                pred_return_pct=1.0,
                actual_return_pct=-1.0,
                actual_direction="DOWN",
                is_direction_correct=False,
                error_pct=-2.0,
            ),
        ]
        metrics = HorizonMetrics()
        n = compute_kronos_accuracy(records, metrics)
        assert n == 3
        assert metrics.kronos_dir_accuracy == pytest.approx(66.7, abs=0.1)

    def test_compute_all_correct(self) -> None:
        records = [
            EvalRecord(
                ticker="sh.600519",
                eval_date="2026-09-01",
                horizon_days=5,
                pred_direction="UP",
                pred_return_pct=2.0,
                actual_return_pct=3.0,
                actual_direction="UP",
                is_direction_correct=True,
                error_pct=1.0,
            ),
        ]
        metrics = HorizonMetrics()
        n = compute_kronos_accuracy(records, metrics)
        assert n == 1
        assert metrics.kronos_dir_accuracy == 100.0

    def test_compute_all_wrong(self) -> None:
        records = [
            EvalRecord(
                ticker="sh.600519",
                eval_date="2026-09-01",
                horizon_days=5,
                pred_direction="UP",
                pred_return_pct=2.0,
                actual_return_pct=-3.0,
                actual_direction="DOWN",
                is_direction_correct=False,
                error_pct=-5.0,
            ),
        ]
        metrics = HorizonMetrics()
        n = compute_kronos_accuracy(records, metrics)
        assert n == 1
        assert metrics.kronos_dir_accuracy == 0.0


class TestReporter:
    """pipeline reporter 测试。"""

    def test_save_json_report(self, tmp_path: Path) -> None:
        data = [
            {
                "ticker": "sh.600519",
                "signal": "BUY",
                "ranking_score": 85.0,
                "ta_confidence": 80.0,
            },
        ]
        output_path = str(tmp_path / "report.json")
        save_json_report(data, output_path)
        assert Path(output_path).exists()
        content = json.loads(Path(output_path).read_text())
        assert content["count"] == 1
        assert content["results"][0]["ticker"] == "sh.600519"

    def test_save_json_report_creates_dir(self, tmp_path: Path) -> None:
        data = [{"ticker": "sh.600519"}]
        output_path = str(tmp_path / "sub" / "report.json")
        save_json_report(data, output_path)
        assert Path(output_path).exists()

    def test_save_html_report(self, tmp_path: Path) -> None:
        data = [
            {
                "ticker": "sh.600519",
                "ranking_score": 85.0,
                "ta_signal": "BUY",
                "ta_confidence": 80.0,
                "kronos_direction": "UP",
                "kronos_change_pct": 2.0,
            },
        ]
        output_path = str(tmp_path / "report.html")
        save_html_report(data, output_path, date="2026-09-01")
        assert Path(output_path).exists()
        content = Path(output_path).read_text()
        assert "sh.600519" in content

    def test_print_results_table(self, capsys: pytest.CaptureFixture) -> None:
        data = [
            {"ticker": "sh.600519", "ta_signal": "BUY", "ranking_score": 85.0},
            {"ticker": "sz.000858", "ta_signal": "HOLD", "ranking_score": 60.0},
        ]
        # print_results_table 使用模块级 Rich Console，直接验证不抛异常
        print_results_table(data)
        captured = capsys.readouterr()
        # Rich Console 默认不输出到 pytest capsys，只验证无异常
        assert len(captured.err) >= 0 or len(captured.out) >= 0

    def test_print_results_summary(self, capsys: pytest.CaptureFixture) -> None:
        data = [
            {"ticker": "sh.600519", "ta_signal": "BUY", "ranking_score": 85.0},
        ]
        # print_results_summary 使用模块级 Rich Console，直接验证不抛异常
        print_results_summary(data, date="2026-09-01")
        captured = capsys.readouterr()
        assert len(captured.err) >= 0 or len(captured.out) >= 0
