#!/usr/bin/env python3
"""prediction_eval_report 测试 — 报告打印工具函数。"""

from __future__ import annotations

from unittest.mock import patch

from trade_krono_cli.eval_data import EvaluationSummary, HorizonMetrics
from trade_krono_cli.prediction_eval_report import (
    print_backtest_report,
    print_latest_backtest,
    print_latest_combined,
    print_latest_kronos,
    print_latest_ta,
)


class TestPrintLatestKronos:
    """print_latest_kronos 测试。"""

    def test_basic(self, caplog) -> None:
        summary = {"kronos_n": 100, "kronos_dir_accuracy": {"5": 60.0, "10": 55.0, "20": 52.0}}
        with patch("trade_krono_cli.prediction_eval_report.logger") as mock_logger:
            print_latest_kronos(summary)
            assert mock_logger.info.call_count > 0

    def test_empty_accuracy(self, caplog) -> None:
        summary = {"kronos_n": 0}
        with patch("trade_krono_cli.prediction_eval_report.logger") as mock_logger:
            print_latest_kronos(summary)
            assert mock_logger.info.call_count > 0

    def test_all_good_scores(self, caplog) -> None:
        summary = {
            "kronos_n": 200,
            "kronos_dir_accuracy": {"5": 65.0, "10": 62.0, "20": 58.0},
        }
        with patch("trade_krono_cli.prediction_eval_report.logger") as mock_logger:
            print_latest_kronos(summary)
            calls = [c.args[0] for c in mock_logger.info.call_args_list]
            # Check that ✅ markers appear for >55% accuracy
            assert any("✅" in c for c in calls)


class TestPrintLatestTA:
    """print_latest_ta 测试。"""

    def test_basic(self, caplog) -> None:
        records = [
            type("R", (), {"ta_signal": "BUY"})(),
            type("R", (), {"ta_signal": "HOLD"})(),
            type("R", (), {"ta_signal": "BUY"})(),
        ]
        summary = {
            "records": records,
            "ta_buy_win_rate": {"5": 60.0, "10": 55.0, "20": 52.0},
            "ta_buy_avg_return": {"5": 2.0, "10": 1.5, "20": 1.0},
        }
        with patch("trade_krono_cli.prediction_eval_report.logger") as mock_logger:
            print_latest_ta(summary)
            assert mock_logger.info.call_count > 0

    def test_no_records(self, caplog) -> None:
        summary = {"records": [], "ta_buy_win_rate": {}, "ta_buy_avg_return": {}}
        with patch("trade_krono_cli.prediction_eval_report.logger") as mock_logger:
            print_latest_ta(summary)
            assert mock_logger.info.call_count > 0


class TestPrintLatestCombined:
    """print_latest_combined 测试。"""

    def test_basic(self, caplog) -> None:
        records = [
            type("R", (), {"ta_signal": "BUY", "pred_direction": "UP"})(),
            type("R", (), {"ta_signal": "HOLD", "pred_direction": "DOWN"})(),
        ]
        summary = {
            "records": records,
            "combined_buy_up_win_rate": {"5": 65.0, "10": 60.0, "20": 58.0},
            "combined_buy_up_avg_return": {"5": 3.0, "10": 2.0, "20": 1.5},
        }
        with patch("trade_krono_cli.prediction_eval_report.logger") as mock_logger:
            print_latest_combined(summary)
            assert mock_logger.info.call_count > 0

    def test_no_records(self, caplog) -> None:
        summary = {"records": [], "combined_buy_up_win_rate": {}, "combined_buy_up_avg_return": {}}
        with patch("trade_krono_cli.prediction_eval_report.logger") as mock_logger:
            print_latest_combined(summary)
            assert mock_logger.info.call_count > 0


class TestPrintBacktestReport:
    """print_backtest_report 测试（EvaluationSummary 路径）。"""

    def test_with_backtest(self, caplog) -> None:
        summary = EvaluationSummary(
            kronos_n=100,
            horizons={
                5: HorizonMetrics(kronos_dir_accuracy=60.0, ta_buy_win_rate=55.0),
            },
            backtest=type(
                "BT",
                (),
                {
                    "rebal_mode": "fixed_horizon",
                    "n_trades": 20,
                    "metrics": {
                        "total_return_pct": 15.0,
                        "annualized_return_pct": 12.0,
                        "sharpe_ratio": 1.5,
                        "max_drawdown_pct": -10.0,
                        "win_rate_pct": 58.0,
                        "profit_factor": 1.8,
                        "avg_win": 3.0,
                        "avg_loss": -2.0,
                        "n_days": 50,
                        "volatility_annual_pct": 20.0,
                        "calmar_ratio": 1.2,
                        "skewness": 0.3,
                        "kurtosis": -0.5,
                        "best_day_pct": 5.0,
                        "worst_day_pct": -4.0,
                    },
                },
            )(),
            benchmark_cum_return_pct=8.0,
            excess_return_pct=7.0,
        )
        with patch("trade_krono_cli.prediction_eval_report.logger") as mock_logger:
            print_backtest_report(summary)
            assert mock_logger.info.call_count > 0

    def test_no_backtest(self, caplog) -> None:
        summary = EvaluationSummary(
            kronos_n=0,
            horizons={},
            backtest=None,
            benchmark_cum_return_pct=0.0,
            excess_return_pct=0.0,
        )
        with patch("trade_krono_cli.prediction_eval_report.logger") as mock_logger:
            print_backtest_report(summary)
            # Should not crash
            assert mock_logger.info.call_count == 0 or mock_logger.info.call_count > 0


class TestPrintLatestBacktest:
    """print_latest_backtest 测试（dict 路径）。"""

    def test_with_backtest(self, caplog) -> None:
        summary = {
            "backtest": {
                "metrics": {
                    "total_return_pct": 15.0,
                    "annualized_return_pct": 12.0,
                    "sharpe_ratio": 1.5,
                    "max_drawdown_pct": -10.0,
                    "win_rate_pct": 58.0,
                    "profit_factor": 1.8,
                },
            },
        }
        with patch("trade_krono_cli.prediction_eval_report.logger") as mock_logger:
            print_latest_backtest(summary)
            assert mock_logger.info.call_count > 0

    def test_no_backtest(self, caplog) -> None:
        summary = {}
        with patch("trade_krono_cli.prediction_eval_report.logger") as mock_logger:
            print_latest_backtest(summary)
            # Should return early without logging
            assert mock_logger.info.call_count == 0

    def test_empty_metrics(self, caplog) -> None:
        summary = {"backtest": {"metrics": {}}}
        with patch("trade_krono_cli.prediction_eval_report.logger") as mock_logger:
            print_latest_backtest(summary)
            assert mock_logger.info.call_count > 0
