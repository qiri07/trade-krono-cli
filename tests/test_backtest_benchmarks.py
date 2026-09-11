#!/usr/bin/env python3
"""backtest_benchmarks 测试。"""

from __future__ import annotations

import pytest

from trade_krono_cli.backtest_benchmarks import (
    BacktestRecord,
    build_backtest_records,
    compute_benchmark_returns,
    compute_excess_curve,
)


class TestBacktestRecord:
    """BacktestRecord 数据类测试。"""

    def test_basic_creation(self) -> None:
        rec = BacktestRecord(
            ticker="sh.600519",
            date="2026-08-11",
            signal="BUY",
            entry_price=180.0,
            exit_price=185.0,
            horizon_days=5,
            pred_direction="UP",
            actual_return_pct=2.78,
        )
        assert rec.ticker == "sh.600519"
        assert rec.signal == "BUY"
        assert rec.horizon_days == 5

    def test_none_fields(self) -> None:
        rec = BacktestRecord(
            ticker="sz.000858",
            date="2026-08-12",
            signal=None,
            entry_price=None,
            exit_price=None,
            horizon_days=0,
            pred_direction=None,
            actual_return_pct=None,
        )
        assert rec.signal is None
        assert rec.entry_price is None


class TestComputeBenchmarkReturns:
    """compute_benchmark_returns 测试。"""

    def test_empty_records(self) -> None:
        result = compute_benchmark_returns([], {})
        assert result == {}

    def test_single_record(self) -> None:
        records = [
            BacktestRecord(
                ticker="sh.600519",
                date="2026-08-11",
                signal="BUY",
                entry_price=180.0,
                exit_price=185.0,
                horizon_days=5,
                pred_direction="UP",
                actual_return_pct=2.78,
            ),
        ]
        records_map: dict[str, list[BacktestRecord]] = {"sh.600519": records}
        result = compute_benchmark_returns(records, records_map)
        # Single day → only initial 0.0
        assert "2026-08-11" in result
        assert result["2026-08-11"] == pytest.approx(0.0, abs=0.01)

    def test_multiple_dates_ticker(self) -> None:
        records = [
            BacktestRecord(ticker="sh.600519", date="2026-08-11", signal="BUY",
                          entry_price=100.0, exit_price=100.0, horizon_days=5,
                          pred_direction="UP", actual_return_pct=None),
            BacktestRecord(ticker="sh.600519", date="2026-08-12", signal="BUY",
                          entry_price=105.0, exit_price=105.0, horizon_days=5,
                          pred_direction="UP", actual_return_pct=None),
            BacktestRecord(ticker="sh.600519", date="2026-08-13", signal="BUY",
                          entry_price=110.0, exit_price=110.0, horizon_days=5,
                          pred_direction="UP", actual_return_pct=None),
        ]
        records_map: dict[str, list[BacktestRecord]] = {"sh.600519": records}
        result = compute_benchmark_returns(records, records_map)
        # Day 1: 0%, Day 2: (105-100)/100 = 5%, Day 3: (110-105)/105 ≈ 4.76%
        assert len(result) == 3
        assert result["2026-08-11"] == pytest.approx(0.0, abs=0.01)
        assert result["2026-08-12"] == pytest.approx(5.0, abs=0.1)

    def test_multiple_tickers(self) -> None:
        records = [
            BacktestRecord(ticker="sh.600519", date="2026-08-11", signal="BUY",
                          entry_price=100.0, exit_price=100.0, horizon_days=5,
                          pred_direction="UP", actual_return_pct=None),
            BacktestRecord(ticker="sh.600519", date="2026-08-12", signal="BUY",
                          entry_price=110.0, exit_price=110.0, horizon_days=5,
                          pred_direction="UP", actual_return_pct=None),
            BacktestRecord(ticker="sz.000858", date="2026-08-11", signal="BUY",
                          entry_price=50.0, exit_price=50.0, horizon_days=5,
                          pred_direction="UP", actual_return_pct=None),
            BacktestRecord(ticker="sz.000858", date="2026-08-12", signal="BUY",
                          entry_price=55.0, exit_price=55.0, horizon_days=5,
                          pred_direction="UP", actual_return_pct=None),
        ]
        records_map: dict[str, list[BacktestRecord]] = {
            "sh.600519": [r for r in records if r.ticker == "sh.600519"],
            "sz.000858": [r for r in records if r.ticker == "sz.000858"],
        }
        result = compute_benchmark_returns(records, records_map)
        # Both tickers up ~10%, so cumulative should be ~10%
        assert result["2026-08-12"] == pytest.approx(10.0, abs=0.5)


class TestComputeExcessCurve:
    """compute_excess_curve 测试。"""

    def test_basic(self) -> None:
        strategy_curve = [("2026-08-11", 0.0), ("2026-08-12", 5.0), ("2026-08-13", 8.0)]
        benchmark_curve = {"2026-08-11": 0.0, "2026-08-12": 3.0, "2026-08-13": 4.0}
        result = compute_excess_curve(strategy_curve, benchmark_curve)
        assert len(result) == 3
        assert result[0] == ("2026-08-11", 0.0)
        # Day 2: excess = (5-3)/100 = 0.02 → cum_excess = 0.02 → *100 = 2.0
        assert result[1] == ("2026-08-12", pytest.approx(2.0, abs=0.1))

    def test_empty_strategy(self) -> None:
        result = compute_excess_curve([], {"2026-08-11": 0.0})
        assert result == []

    def test_benchmark_missing_day(self) -> None:
        strategy_curve = [("2026-08-11", 5.0)]
        benchmark_curve = {}
        result = compute_excess_curve(strategy_curve, benchmark_curve)
        # Benchmark missing → uses 0.0
        assert len(result) == 1


class TestBuildBacktestRecords:
    """build_backtest_records 测试。"""

    def test_filter_by_horizon(self) -> None:
        class FakeRecord:
            def __init__(self, ticker: str, eval_date: str, horizon_days: int,
                         ta_signal: str | None, pred_direction: str | None,
                         actual_return_pct: float | None) -> None:
                self.ticker = ticker
                self.eval_date = eval_date
                self.horizon_days = horizon_days
                self.ta_signal = ta_signal
                self.pred_direction = pred_direction
                self.actual_return_pct = actual_return_pct

        records = [
            FakeRecord("sh.600519", "2026-08-11", 5, "BUY", "UP", 2.0),
            FakeRecord("sh.600519", "2026-08-12", 5, "BUY", "UP", 1.5),
            FakeRecord("sz.000858", "2026-08-11", 10, "SELL", "DOWN", -1.0),
            FakeRecord("sh.600519", "2026-08-13", 5, "HOLD", "FLAT", 0.0),
        ]
        result = build_backtest_records(records, horizon=5)
        assert len(result) == 3
        assert all(r.horizon_days == 5 for r in result)

    def test_empty_input(self) -> None:
        result = build_backtest_records([], horizon=5)
        assert result == []

    def test_no_matching_horizon(self) -> None:
        class FakeRecord:
            def __init__(self, ticker: str, eval_date: str, horizon_days: int,
                         ta_signal: str | None, pred_direction: str | None,
                         actual_return_pct: float | None) -> None:
                self.ticker = ticker
                self.eval_date = eval_date
                self.horizon_days = horizon_days
                self.ta_signal = ta_signal
                self.pred_direction = pred_direction
                self.actual_return_pct = actual_return_pct

        records = [FakeRecord("sh.600519", "2026-08-11", 10, "BUY", "UP", 2.0),
                   FakeRecord("sz.000858", "2026-08-12", 20, "SELL", "DOWN", -1.0)]
        result = build_backtest_records(records, horizon=5)
        assert result == []
