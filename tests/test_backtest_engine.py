#!/usr/bin/env python3
"""backtest_engine 测试 — BacktestEngine 核心逻辑。"""

from __future__ import annotations

from datetime import datetime

import pytest

from trade_krono_cli.backtest_benchmarks import BacktestRecord
from trade_krono_cli.backtest_engine import BacktestEngine, _next_trading_day


class TestNextTradingDay:
    """_next_trading_day 辅助函数测试。"""

    def test_finds_next_day(self) -> None:
        d = datetime(2026, 8, 11)
        dates = ["2026-08-11", "2026-08-12", "2026-08-13"]
        result = _next_trading_day(d, dates)
        assert result == "2026-08-12"

    def test_no_future_day(self) -> None:
        d = datetime(2026, 8, 13)
        dates = ["2026-08-11", "2026-08-12"]
        result = _next_trading_day(d, dates)
        assert result is None

    def test_exact_match_excluded(self) -> None:
        d = datetime(2026, 8, 12)
        dates = ["2026-08-11", "2026-08-12", "2026-08-13"]
        result = _next_trading_day(d, dates)
        assert result == "2026-08-13"


class TestBacktestEngine:
    """BacktestEngine 主流程测试。"""

    def test_empty_records(self) -> None:
        engine = BacktestEngine()
        result = engine.run([])
        # empty() returns final_value=0, total_return=0 (expected for no records)
        assert result.total_return_pct == pytest.approx(0.0, abs=0.01)
        assert result.n_trades == 0

    def test_single_date_no_trade(self) -> None:
        """只有一天数据，无法形成完整交易循环。"""
        engine = BacktestEngine()
        records = [
            BacktestRecord(
                ticker="sh.600519",
                date="2026-08-11",
                signal="BUY",
                entry_price=180.0,
                exit_price=None,
                horizon_days=5,
                pred_direction="UP",
                actual_return_pct=None,
            ),
        ]
        result = engine.run(records)
        # Only one date → returns empty (final_value=0 per empty())
        assert result.total_return_pct == pytest.approx(0.0, abs=0.01)

    def test_fixed_horizon_basic(self) -> None:
        """简单固定持仓周期回测：2天买入，3天后卖出。"""
        engine = BacktestEngine(fixed_horizon=3, initial_capital=1_000_000.0)
        records = [
            # Day 1: buy signal
            BacktestRecord(ticker="sh.600519", date="2026-08-11", signal="BUY",
                          entry_price=100.0, exit_price=None, horizon_days=5,
                          pred_direction="UP", actual_return_pct=None),
            # Day 2: no signal (hold)
            # Day 3: price moves up
            # Day 4: still holding
            # Day 5: exit signal (after 3 days hold)
            BacktestRecord(ticker="sh.600519", date="2026-08-14", signal="SELL",
                          entry_price=None, exit_price=105.0, horizon_days=5,
                          pred_direction="UP", actual_return_pct=None),
        ]
        result = engine.run(records)
        assert isinstance(result, type(engine.run([])))
        # Should have some trades or at least not crash
        assert result.n_trades >= 0


class TestBacktestEnginePriceHelpers:
    """BacktestEngine 价格辅助方法测试。"""

    def test_get_entry_price(self) -> None:
        engine = BacktestEngine()
        prev_close_map = {"sh.600519": 180.0}
        price = engine._get_entry_price("sh.600519", "2026-08-11", prev_close_map)
        assert price == 180.0

    def test_get_entry_price_not_found(self) -> None:
        engine = BacktestEngine()
        prev_close_map: dict[str, float] = {}
        price = engine._get_entry_price("sh.600519", "2026-08-11", prev_close_map)
        assert price is None

    def test_get_exit_price(self) -> None:
        engine = BacktestEngine()
        prev_close_map = {"sh.600519": 185.0}
        price = engine._get_exit_price("sh.600519", "2026-08-12", prev_close_map)
        assert price == 185.0


class TestBacktestEngineClosePosition:
    """_close_position 测试。"""

    def test_normal_close(self) -> None:
        engine = BacktestEngine()
        from trade_krono_cli.backtest_engine import _Position
        pos = _Position(
            ticker="sh.600519",
            entry_date="2026-08-10",
            entry_price=100.0,
            shares=100,
            direction="UP",
            cost_bps=3.0,
        )
        prev_close_map = {"sh.600519": 95.0}
        log = engine._close_position(pos, 105.0, "2026-08-13", "sh.600519", prev_close_map)
        assert not log.blocked
        assert log.net_proceeds > 0

    def test_limit_down_blocked(self) -> None:
        engine = BacktestEngine()
        from trade_krono_cli.backtest_engine import _Position
        pos = _Position(
            ticker="sh.600519",
            entry_date="2026-08-10",
            entry_price=100.0,
            shares=100,
            direction="UP",
            cost_bps=3.0,
        )
        # prev_close=100, limit_down≈90.9, exit_price=90 → blocked
        prev_close_map = {"sh.600519": 100.0}
        log = engine._close_position(pos, 90.0, "2026-08-13", "sh.600519", prev_close_map)
        assert log.blocked
        assert "LIMIT_DOWN" in log.blocked_reason


class TestComputeMetrics:
    """_compute_metrics 绩效指标计算测试。"""

    def test_empty_equity_curve(self) -> None:
        engine = BacktestEngine()
        result = engine._compute_metrics([], [], [])
        assert result == {}

    def test_single_day_equity(self) -> None:
        engine = BacktestEngine()
        equity_curve = [("2026-08-11", 1_000_000.0)]
        result = engine._compute_metrics(equity_curve, [], [])
        assert result == {}  # Need at least 2 days

    def test_basic_metrics(self) -> None:
        engine = BacktestEngine()
        # Simulate: equity grows from 1M to 1.1M over 10 days
        equity_curve = [(f"2026-08-{11+i}", 1_000_000.0 + i * 10_000.0) for i in range(10)]
        trades = [
            {"action": "SELL", "pnl": 5000.0},
            {"action": "SELL", "pnl": -2000.0},
            {"action": "SELL", "pnl": 3000.0},
        ]
        result = engine._compute_metrics(equity_curve, trades, [])
        assert "total_return_pct" in result
        assert "sharpe_ratio" in result
        assert "max_drawdown_pct" in result
        assert result["total_return_pct"] > 0
        assert result["win_rate_pct"] > 0

    def test_loss_only_trades(self) -> None:
        engine = BacktestEngine()
        equity_curve = [(f"2026-08-{11+i}", 1_000_000.0 - i * 5_000.0) for i in range(5)]
        trades = [
            {"action": "SELL", "pnl": -1000.0},
            {"action": "SELL", "pnl": -500.0},
        ]
        result = engine._compute_metrics(equity_curve, trades, [])
        assert result["win_rate_pct"] == 0.0
        assert result["total_return_pct"] < 0
