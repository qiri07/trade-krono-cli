"""测试回测引擎（backtest_engine.py）。"""
import pytest
from unittest.mock import MagicMock, patch
import pandas as pd
from datetime import datetime, timedelta

from trade_krono_cli.backtest_engine import (
    BacktestEngine,
    BacktestRecord,
    build_backtest_records,
    compute_benchmark_returns,
    _next_trading_day,
    _week_start,
    _month_start,
)
from trade_krono_cli.eval_data import EvalRecord, EvaluationSummary


# ── 辅助函数测试 ──────────────────────────────────────────────────────────────

def test_next_trading_day():
    """_next_trading_day 应在 kline_dates 中找最近的未来交易日。"""
    dates = ["2026-01-01", "2026-01-02", "2026-01-05", "2026-01-06"]
    assert _next_trading_day(datetime(2026, 1, 1), dates) == "2026-01-02"
    assert _next_trading_day(datetime(2026, 1, 2), dates) == "2026-01-05"
    assert _next_trading_day(datetime(2026, 1, 5), dates) == "2026-01-06"
    assert _next_trading_day(datetime(2026, 1, 6), dates) is None


def test_week_start():
    assert _week_start(datetime(2026, 1, 5)) == datetime(2026, 1, 5)  # Monday
    assert _week_start(datetime(2026, 1, 6)) == datetime(2026, 1, 5)  # Tuesday


def test_month_start():
    assert _month_start(datetime(2026, 1, 15)) == datetime(2026, 1, 1)
    assert _month_start(datetime(2026, 3, 1)) == datetime(2026, 3, 1)


# ── BacktestRecord / build_backtest_records ───────────────────────────────────

def test_build_backtest_records_filters_horizon():
    """build_backtest_records 应按 horizon 筛选记录。"""
    records = [
        EvalRecord(
            ticker="sh.600519", eval_date="2026-01-01", horizon_days=5,
            pred_direction="UP", pred_return_pct=3.0,
            actual_return_pct=2.5, actual_direction="UP",
            is_direction_correct=True, error_pct=0.5,
            ta_signal="BUY", composite_score=80.0,
        ),
        EvalRecord(
            ticker="sh.600519", eval_date="2026-01-01", horizon_days=10,
            pred_direction="UP", pred_return_pct=5.0,
            actual_return_pct=4.0, actual_direction="UP",
            is_direction_correct=True, error_pct=1.0,
            ta_signal="BUY", composite_score=75.0,
        ),
    ]
    bt = build_backtest_records(records, horizon=5)
    assert len(bt) == 1
    assert bt[0].horizon_days == 5
    assert bt[0].signal == "BUY"


def test_build_backtest_records_empty():
    records = []
    bt = build_backtest_records(records, horizon=5)
    assert bt == []


# ── BacktestEngine 基本运行 ───────────────────────────────────────────────────

def _make_eval_records(n=5, horizon=5):
    """构造 n 条模拟评估记录（不同日期、不同股票）。"""
    records = []
    base_date = datetime(2026, 1, 5)
    for i in range(n):
        date = (base_date + timedelta(days=i * 5)).strftime("%Y-%m-%d")
        records.append(EvalRecord(
            ticker=f"sh.600{i:03d}", eval_date=date, horizon_days=horizon,
            pred_direction="UP", pred_return_pct=3.0,
            actual_return_pct=2.0 + i * 0.3, actual_direction="UP",
            is_direction_correct=True, error_pct=1.0,
            ta_signal="BUY" if i % 2 == 0 else "HOLD",
            composite_score=70.0 + i * 2,
        ))
    return records


class TestBacktestEngineBasic:
    """BacktestEngine 基本功能测试。"""

    def test_empty_records(self):
        engine = BacktestEngine()
        result = engine.run([])
        assert result.initial_capital == 1_000_000.0
        assert result.total_return_pct == 0.0
        assert result.n_trades == 0

    def test_run_with_mock_prices(self, tmp_path):
        """使用 mock 价格运行回测，验证基本流程不崩溃。"""
        from trade_krono_cli.research_db import ResearchDatabase
        from trade_krono_cli.prediction_eval import PredictionEvaluator

        db = tmp_path / "bt_test.db"
        research = ResearchDatabase(db_path=db)

        # 创建 job + 信号
        job_id = research.create_job("2026-01-01", ["sh.600519"])
        import sqlite3
        with sqlite3.connect(db) as conn:
            conn.execute(
                "INSERT INTO signals (job_id, ticker, rank, composite_score, "
                " ta_signal, ta_confidence, ta_reasoning, kronos_direction, "
                " kronos_change, ta_error, kronos_error) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (job_id, "sh.600519", 1, 80.0, "BUY", 85.0, "test",
                 "UP", 3.0, None, None),
            )
            conn.commit()

        evaluator = PredictionEvaluator.__new__(PredictionEvaluator)
        evaluator._research = research
        evaluator.HORIZONS = [5]

        # mock 价格获取：entry=100, exit=105
        def fake_get_close(ticker, date_str):
            if "2026-01-01" in date_str:
                return 100.0
            elif "2026-01-06" in date_str:
                return 105.0
            return None

        with patch("trade_krono_cli.prediction_eval._get_close_price", side_effect=fake_get_close):
            with patch("trade_krono_cli.prediction_eval._get_kline_window", return_value=None):
                summary = evaluator.evaluate(store=False, backtest=True)

        # 应有回测结果
        assert summary.backtest is not None
        assert summary.backtest.total_return_pct != 0.0 or summary.backtest.n_trades == 0

    def test_metrics_computation(self):
        """回测引擎应能计算完整绩效指标。"""
        engine = BacktestEngine(initial_capital=1_000_000.0)

        # 模拟 equity curve：5 天，每天 +1%
        equity = [
            ("2026-01-01", 1_000_000.0),
            ("2026-01-02", 1_010_000.0),
            ("2026-01-05", 1_020_100.0),
            ("2026-01-06", 1_030_301.0),
            ("2026-01-07", 1_040_604.0),
        ]
        trades = [
            {"date": "2026-01-02", "action": "BUY", "ticker": "sh.600519"},
            {"date": "2026-01-06", "action": "SELL", "ticker": "sh.600519", "pnl": 30_301.0},
        ]
        records = [BacktestRecord(
            ticker="sh.600519", date="2026-01-01", signal="BUY",
            entry_price=100.0, exit_price=105.0,
            horizon_days=5, pred_direction="UP", actual_return_pct=5.0,
        )]

        m = engine._compute_metrics(equity, trades, records)
        assert m["total_return_pct"] > 0
        assert m["annualized_return_pct"] > 0
        assert m["sharpe_ratio"] > 0
        assert m["win_rate_pct"] == 100.0  # 1 win, 0 loss
        assert m["n_trades"] == 2
        assert m["n_days"] == 5

    def test_metrics_empty_curve(self):
        engine = BacktestEngine()
        m = engine._compute_metrics([], [], [])
        assert m == {}

    def test_metrics_single_day(self):
        """单天 equity curve 不应崩溃，指标应为空。"""
        engine = BacktestEngine()
        equity = [("2026-01-01", 1_000_000.0)]
        m = engine._compute_metrics(equity, [], [])
        assert m == {}  # n_days < 2 直接返回空

    def test_calmar_ratio_with_drawdown(self):
        """有回撤时卡玛比率符号应与总收益一致。"""
        engine = BacktestEngine()
        # 先涨后跌但总收益为正：100 → 150 → 120
        equity = [
            ("2026-01-01", 100.0),
            ("2026-01-02", 150.0),
            ("2026-01-05", 120.0),
        ]
        m = engine._compute_metrics(equity, [], [])
        # 总收益 +20%，最大回撤 -20%（从150到120），calmar = 20/20 = 1.0
        assert m["calmar_ratio"] > 0
        assert m["total_return_pct"] == pytest.approx(20.0, abs=1.0)
        assert m["max_drawdown_pct"] == pytest.approx(-20.0, abs=1.0)


# ── 绩效指标数值测试 ──────────────────────────────────────────────────────────

class TestMetricsComputation:
    """绩效指标计算的正确性验证。"""

    def test_sharpe_positive_returns(self):
        """正收益序列（有波动）→ 正夏普比率。"""
        engine = BacktestEngine()
        # 5 天，有波动的正收益
        values = [1_000_000, 1_010_000, 1_005_000, 1_020_000, 1_015_000]
        equity = [(f"2026-01-{i+1:02d}", v) for i, v in enumerate(values)]
        m = engine._compute_metrics(equity, [], [])
        assert m["sharpe_ratio"] > 0

    def test_sharpe_negative_returns(self):
        """负收益序列 → 负夏普比率。"""
        engine = BacktestEngine()
        values = [1_000_000, 990_000, 995_000, 980_000, 970_000]
        equity = [(f"2026-01-{i+1:02d}", v) for i, v in enumerate(values)]
        m = engine._compute_metrics(equity, [], [])
        assert m["sharpe_ratio"] < 0

    def test_max_drawdown_calculation(self):
        """正确计算最大回撤。"""
        engine = BacktestEngine()
        # 先涨后跌：100 → 120 → 90
        equity = [
            ("2026-01-01", 100.0),
            ("2026-01-02", 120.0),
            ("2026-01-05", 90.0),
        ]
        m = engine._compute_metrics(equity, [], [])
        assert m["max_drawdown_pct"] == pytest.approx(-25.0, abs=1.0)

    def test_calmar_ratio(self):
        """卡玛比率 = 年化收益 / |最大回撤|（有回撤时为正）。"""
        engine = BacktestEngine()
        # 先涨后回撤：100 → 130 → 110
        equity = [
            ("2026-01-01", 100.0),
            ("2026-01-02", 130.0),
            ("2026-01-05", 110.0),
        ]
        m = engine._compute_metrics(equity, [], [])
        assert m["calmar_ratio"] > 0

    def test_win_loss_ratio(self):
        """盈亏比 = 平均盈利 / 平均亏损。"""
        engine = BacktestEngine()
        # 至少 2 天才能计算指标
        equity = [("2026-01-01", 1_000_000.0), ("2026-01-02", 1_000_000.0)]
        trades = [
            {"action": "SELL", "pnl": 1000.0},
            {"action": "SELL", "pnl": -500.0},
            {"action": "SELL", "pnl": 2000.0},
        ]
        m = engine._compute_metrics(equity, trades, [])
        assert m["n_wins"] == 2
        assert m["n_losses"] == 1
        assert m["profit_factor"] == pytest.approx(3000.0 / 500.0, abs=0.1)

    def test_annualized_return(self):
        """年化收益率不应出现荒谬数值。"""
        engine = BacktestEngine()
        # 500 天，总收益 ~10%
        values = [1_000_000 * (1.0002 ** i) for i in range(500)]
        equity = [(f"2026-01-{i+1:02d}" if i < 31 else f"2026-02-{i-30+1:02d}", v)
                  for i, v in enumerate(values)]
        m = engine._compute_metrics(equity, [], [])
        assert 5.0 < m["annualized_return_pct"] < 10.0  # ~10%/年复利
        assert m["total_return_pct"] == pytest.approx(10.0, abs=1.0)

    def test_distribution_stats(self):
        """收益分布统计（偏度、峰度、极值）应合理。"""
        engine = BacktestEngine()
        import numpy as np
        np.random.seed(42)
        daily_rets = np.random.normal(0.001, 0.02, 100)
        values = [1_000_000 * np.cumprod(1 + daily_rets)[i] for i in range(100)]
        equity = [(f"2026-04-{i+1:02d}", v) for i, v in enumerate(values)]
        m = engine._compute_metrics(equity, [], [])
        assert "skewness" in m
        assert "kurtosis" in m
        assert m["best_day_pct"] > 0
        assert m["worst_day_pct"] < 0  # 随机序列必有负收益日


# ── 基准计算 ──────────────────────────────────────────────────────────────────

class TestBenchmarkReturns:
    """基准收益计算测试。"""

    def test_empty_records(self):
        result = compute_benchmark_returns([], {})
        assert result == {}

    def test_single_ticker(self):
        """单只股票等权组合应产生基准曲线。"""
        records = [
            BacktestRecord(
                ticker="sh.600519", date="2026-01-01", signal="BUY",
                entry_price=100.0, exit_price=105.0,
                horizon_days=5, pred_direction="UP", actual_return_pct=5.0,
            ),
            BacktestRecord(
                ticker="sh.600519", date="2026-01-06", signal="BUY",
                entry_price=105.0, exit_price=103.0,
                horizon_days=5, pred_direction="DOWN", actual_return_pct=-1.9,
            ),
        ]
        result = compute_benchmark_returns(records, {})
        assert len(result) >= 2
        # 第一天 cum_ret = 0（基准起始）
        assert list(result.values())[0] == 0.0


# ── 端到端集成测试 ────────────────────────────────────────────────────────────

class TestEndToEnd:
    """端到端回测集成测试。"""

    def test_evaluate_with_backtest_flag(self, tmp_path):
        """evaluate(backtest=True) 应返回带 backtest 结果的 summary。"""
        from trade_krono_cli.research_db import ResearchDatabase
        from trade_krono_cli.prediction_eval import PredictionEvaluator

        db = tmp_path / "bt_end2end.db"
        research = ResearchDatabase(db_path=db)

        job_id = research.create_job("2026-01-01", ["sh.600519", "sz.000858"])
        import sqlite3
        with sqlite3.connect(db) as conn:
            for i, tk in enumerate(["sh.600519", "sz.000858"]):
                conn.execute(
                    "INSERT INTO signals (job_id, ticker, rank, composite_score, "
                    " ta_signal, ta_confidence, ta_reasoning, kronos_direction, "
                    " kronos_change, ta_error, kronos_error) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (job_id, tk, i + 1, 75.0, "BUY", 80.0, "test",
                     "UP", 3.0, None, None),
                )
            conn.commit()

        evaluator = PredictionEvaluator.__new__(PredictionEvaluator)
        evaluator._research = research
        evaluator.HORIZONS = [5]

        def fake_get_close(ticker, date_str):
            if "2026-01-01" in date_str:
                return 100.0
            elif "2026-01-06" in date_str:
                return 105.0
            return None

        with patch("trade_krono_cli.prediction_eval._get_close_price", side_effect=fake_get_close):
            with patch("trade_krono_cli.prediction_eval._get_kline_window", return_value=None):
                summary = evaluator.evaluate(store=False, backtest=True)

        assert summary.backtest is not None
        assert isinstance(summary.backtest.total_return_pct, float)

    def test_evaluate_without_backtest_flag(self, tmp_path):
        """默认 backtest=False 时，summary.backtest 应为 None。"""
        from trade_krono_cli.research_db import ResearchDatabase
        from trade_krono_cli.prediction_eval import PredictionEvaluator

        db = tmp_path / "no_bt.db"
        research = ResearchDatabase(db_path=db)
        job_id = research.create_job("2026-01-01", ["sh.600519"])
        import sqlite3
        with sqlite3.connect(db) as conn:
            conn.execute(
                "INSERT INTO signals (job_id, ticker, rank, composite_score, "
                " ta_signal, ta_confidence, ta_reasoning, kronos_direction, "
                " kronos_change, ta_error, kronos_error) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (job_id, "sh.600519", 1, 75.0, "BUY", 80.0, "test",
                 "UP", 3.0, None, None),
            )
            conn.commit()

        evaluator = PredictionEvaluator.__new__(PredictionEvaluator)
        evaluator._research = research
        evaluator.HORIZONS = [5]

        summary = evaluator.evaluate(store=False, backtest=False)
        assert summary.backtest is None

    def test_run_evaluation_with_backtest_cli(self, caplog):
        """run_evaluation(backtest=True) 应打印回测报告。"""
        from trade_krono_cli.prediction_eval import run_evaluation
        from trade_krono_cli.prediction_eval import PredictionEvaluator
        from loguru import logger

        captured_lines: list[str] = []
        original_info = logger.info

        def capture_info(*args, **kwargs):
            if args:
                captured_lines.append(str(args[0]))

        with patch.object(PredictionEvaluator, '__init__', lambda self, **kw: None):
            fake_eval = MagicMock()
            fake_summary = EvaluationSummary()
            from trade_krono_cli.eval_data import BacktestResult
            fake_summary.backtest = BacktestResult(
                initial_capital=1_000_000.0,
                final_value=1_125_000.0,
                total_return_pct=12.5,
                metrics={
                    "total_return_pct": 12.5,
                    "annualized_return_pct": 25.0,
                    "sharpe_ratio": 1.5,
                    "max_drawdown_pct": -8.0,
                    "win_rate_pct": 60.0,
                    "profit_factor": 2.0,
                },
                equity_curve=[],
                n_trades=10,
                rebal_mode="fixed_horizon",
            )
            fake_eval.evaluate.return_value = fake_summary
            fake_eval.print_report = MagicMock()
            with patch("trade_krono_cli.prediction_eval.PredictionEvaluator", return_value=fake_eval):
                with patch.object(logger, "info", capture_info):
                    run_evaluation(backtest=True)
            full_output = "\n".join(captured_lines)
            assert "回测" in full_output or "总收益率" in full_output
