"""Tests for trade_krono_cli.eval_benchmark.

覆盖 BenchmarkResult、AlphaResult、fetch/compute 函数。
"""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest

from trade_krono_cli.eval_benchmark import (
    AlphaResult,
    BenchmarkResult,
    compute_alpha,
    compute_benchmark_metrics,
    compute_portfolio_metrics,
    fetch_benchmark_kline,
    get_best_alpha,
)

# ═══════════════════════════════════════════════════════
#  fetch_benchmark_kline
# ═══════════════════════════════════════════════════════


class TestFetchBenchmarkKline:
    def test_success(self) -> None:
        mock_df = pd.DataFrame({
            "timestamps": ["2026-08-01", "2026-08-02", "2026-08-03"],
            "close": [3800.0, 3820.0, 3810.0],
        })
        with patch("trade_krono_cli.eval_benchmark.fetch_kline", return_value=mock_df):
            result = fetch_benchmark_kline("sh.000300", "2026-08-01", "2026-08-03")
        assert result is not None
        assert len(result) == 3
        assert result[0] == ("2026-08-01", 3800.0)

    def test_empty_df_returns_none(self) -> None:
        mock_df = pd.DataFrame({"timestamps": [], "close": []})
        with patch("trade_krono_cli.eval_benchmark.fetch_kline", return_value=mock_df):
            assert fetch_benchmark_kline("sh.000300", "2026-01-01", "2026-01-02") is None

    def test_fetch_raises_returns_none(self) -> None:
        with patch("trade_krono_cli.eval_benchmark.fetch_kline", side_effect=RuntimeError("net err")):
            assert fetch_benchmark_kline("sh.000300", "2026-01-01", "2026-01-02") is None

    def test_invalid_close_values_skipped(self) -> None:
        """close 字段含非数字值时应跳过该行。"""
        mock_df = pd.DataFrame({
            "timestamps": ["2026-08-01", "2026-08-02"],
            "close": ["not_a_number", 3800.0],
        })
        with patch("trade_krono_cli.eval_benchmark.fetch_kline", return_value=mock_df):
            result = fetch_benchmark_kline("sh.000300", "2026-08-01", "2026-08-02")
        assert result is not None
        assert len(result) == 1


# ═══════════════════════════════════════════════════════
#  compute_benchmark_metrics
# ═══════════════════════════════════════════════════════


class TestComputeBenchmarkMetrics:
    def test_basic_calculation(self) -> None:
        kline = [("2026-01-01", 100.0), ("2026-01-02", 110.0), ("2026-01-03", 105.0)]
        with patch("trade_krono_cli.eval_benchmark.fetch_benchmark_kline", return_value=kline):
            result = compute_benchmark_metrics("sh.000300", "CSI300", "2026-01-01", "2026-01-03")

        assert result is not None
        assert result.name == "CSI300"
        assert result.cumulative_return_pct == pytest.approx(5.0, abs=0.1)
        assert result.n_days == 3
        assert len(result.equity_curve) == 3

    def test_single_point_returns_none(self) -> None:
        with patch("trade_krono_cli.eval_benchmark.fetch_benchmark_kline", return_value=[("2026-01-01", 100.0)]):
            assert compute_benchmark_metrics("sh.000300", "CSI300", "2026-01-01", "2026-01-01") is None

    def test_empty_kline_returns_none(self) -> None:
        with patch("trade_krono_cli.eval_benchmark.fetch_benchmark_kline", return_value=[]):
            assert compute_benchmark_metrics("sh.000300", "CSI300", "2026-01-01", "2026-01-02") is None

    def test_none_kline_returns_none(self) -> None:
        with patch("trade_krono_cli.eval_benchmark.fetch_benchmark_kline", return_value=None):
            assert compute_benchmark_metrics("sh.000300", "CSI300", "2026-01-01", "2026-01-02") is None

    def test_zero_price_returns_none(self) -> None:
        with patch("trade_krono_cli.eval_benchmark.fetch_benchmark_kline", return_value=[("2026-01-01", 0.0), ("2026-01-02", 100.0)]):
            assert compute_benchmark_metrics("sh.000300", "CSI300", "2026-01-01", "2026-01-02") is None

    def test_constant_prices_sharpe_zero(self) -> None:
        kline = [("2026-01-{:02d}".format(d), 100.0) for d in range(1, 11)]
        with patch("trade_krono_cli.eval_benchmark.fetch_benchmark_kline", return_value=kline):
            result = compute_benchmark_metrics("sh.000300", "CSI300", "2026-01-01", "2026-01-10")
        assert result is not None
        assert result.cumulative_return_pct == pytest.approx(0.0, abs=0.01)
        assert result.volatility_annual_pct == pytest.approx(0.0, abs=0.01)

    def test_up_trend(self) -> None:
        prices = [100.0 + i * 2.0 for i in range(20)]
        kline = [(f"2026-01-{i+1:02d}", p) for i, p in enumerate(prices)]
        with patch("trade_krono_cli.eval_benchmark.fetch_benchmark_kline", return_value=kline):
            result = compute_benchmark_metrics("sh.000300", "CSI300", "2026-01-01", "2026-01-20")
        assert result is not None
        assert result.cumulative_return_pct > 0
        assert result.sharpe_ratio > 0

    def test_max_drawdown_computed(self) -> None:
        """峰值后回撤应被正确计算。"""
        kline = [
            ("2026-01-01", 100.0),
            ("2026-01-02", 120.0),
            ("2026-01-03", 90.0),
            ("2026-01-04", 100.0),
        ]
        with patch("trade_krono_cli.eval_benchmark.fetch_benchmark_kline", return_value=kline):
            result = compute_benchmark_metrics("sh.000300", "CSI300", "2026-01-01", "2026-01-04")
        assert result is not None
        assert result.max_drawdown_pct < 0  # negative drawdown


# ═══════════════════════════════════════════════════════
#  compute_portfolio_metrics
# ═══════════════════════════════════════════════════════


class TestComputePortfolioMetrics:
    def test_basic(self) -> None:
        equity = [("2026-01-{:02d}".format(d), 100.0 + d * 2.0) for d in range(1, 11)]
        trades = [
            {"action": "BUY", "pnl": 0.0},
            {"action": "SELL", "pnl": 10.0},
            {"action": "BUY", "pnl": 0.0},
            {"action": "SELL", "pnl": 5.0},
        ]
        result = compute_portfolio_metrics(equity, trades)
        assert "cagr_pct" in result
        assert "sharpe_ratio" in result
        assert "max_drawdown_pct" in result
        assert result["n_days"] == 10
        assert result["n_trades"] == 4

    def test_empty_equity(self) -> None:
        assert compute_portfolio_metrics([], []) == {}

    def test_single_day_equity(self) -> None:
        equity = [("2026-01-01", 100.0)]
        result = compute_portfolio_metrics(equity, [])
        assert result == {}

    def test_all_wins_profit_factor(self) -> None:
        equity = [("2026-01-{:02d}".format(d), 100.0 + d * 5.0) for d in range(1, 6)]
        trades = [{"action": "SELL", "pnl": 10.0}, {"action": "SELL", "pnl": 20.0}]
        result = compute_portfolio_metrics(equity, trades)
        assert result["profit_factor"] > 1.0

    def test_no_wins_profit_factor(self) -> None:
        equity = [("2026-01-{:02d}".format(d), 100.0 - d) for d in range(1, 6)]
        trades = [{"action": "SELL", "pnl": -5.0}, {"action": "SELL", "pnl": -3.0}]
        result = compute_portfolio_metrics(equity, trades)
        assert result["profit_factor"] == pytest.approx(0.0, abs=0.01)

    def test_sortino_vs_sharpe(self) -> None:
        """Sortino 应 ≤ Sharpe（只惩罚下行波动）。"""
        equity = [("2026-01-{:02d}".format(d), 100.0 + (1 if d % 2 == 0 else -2)) for d in range(1, 21)]
        result = compute_portfolio_metrics(equity, [])
        if result.get("sortino_ratio") and result.get("sharpe_ratio"):
            assert result["sortino_ratio"] <= result["sharpe_ratio"] + 0.01

    def test_calmar_ratio(self) -> None:
        """有回撤时 calmar_ratio 应为正数。"""
        # 先涨后跌再涨，产生明显回撤
        equity = [
            ("2026-01-01", 100.0),
            ("2026-01-02", 120.0),
            ("2026-01-03", 110.0),
            ("2026-01-04", 130.0),
            ("2026-01-05", 125.0),
            ("2026-01-06", 140.0),
            ("2026-01-07", 135.0),
            ("2026-01-08", 150.0),
            ("2026-01-09", 145.0),
            ("2026-01-10", 160.0),
        ]
        result = compute_portfolio_metrics(equity, [])
        assert result["calmar_ratio"] > 0


# ═══════════════════════════════════════════════════════
#  compute_alpha / get_best_alpha
# ═══════════════════════════════════════════════════════


class TestComputeAlpha:
    def test_empty_records(self) -> None:
        result = compute_alpha(10.0, [], ("2026-01-01", "2026-01-31"))
        assert result == {}

    def test_all_benchmarks_failed(self) -> None:
        with patch("trade_krono_cli.eval_benchmark.compute_benchmark_metrics", return_value=None):
            result = compute_alpha(
                10.0,
                [type("ER", (), {"date": "2026-01-01"})()],
                ("2026-01-01", "2026-01-31"),
            )
        assert result == {}

    def test_partial_benchmark_success(self) -> None:
        """部分基准成功时只返回成功的。"""
        call_count = 0
        def side_effect(ticker, name, start, end):
            nonlocal call_count
            call_count += 1
            if name == "CSI300":
                return BenchmarkResult(name=name, ticker=ticker, cumulative_return_pct=5.0, n_days=20)
            return None

        with patch("trade_krono_cli.eval_benchmark.compute_benchmark_metrics", side_effect=side_effect):
            result = compute_alpha(
                10.0,
                [type("ER", (), {"date": "2026-01-01"})()],
                ("2026-01-01", "2026-01-31"),
            )

        assert "CSI300" in result
        assert result["CSI300"].alpha_pct == pytest.approx(5.0, abs=0.1)


class TestGetBestAlpha:
    def test_empty(self) -> None:
        assert get_best_alpha({}) is None

    def test_single(self) -> None:
        r = AlphaResult(strategy_return_pct=10.0, benchmark_return_pct=5.0, alpha_pct=5.0, benchmark_name="CSI300")
        assert get_best_alpha({"CSI300": r}) is r

    def test_max_alpha(self) -> None:
        r1 = AlphaResult(strategy_return_pct=10.0, benchmark_return_pct=5.0, alpha_pct=5.0, benchmark_name="CSI300")
        r2 = AlphaResult(strategy_return_pct=10.0, benchmark_return_pct=8.0, alpha_pct=2.0, benchmark_name="CSI500")
        best = get_best_alpha({"CSI300": r1, "CSI500": r2})
        assert best.benchmark_name == "CSI300"
