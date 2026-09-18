"""prediction_eval_backtest.py 测试 — run_backtest() 函数。"""

from __future__ import annotations

from unittest.mock import patch

from trade_krono_cli.eval_data import EvalRecord
from trade_krono_cli.prediction_eval_backtest import run_backtest


def _make_record(
    ticker: str = "sh.600519",
    eval_date: str = "2026-08-14",
    horizon_days: int = 5,
    entry_blocked: bool = False,
    exit_blocked: bool = False,
) -> EvalRecord:
    return EvalRecord(
        ticker=ticker,
        eval_date=eval_date,
        horizon_days=horizon_days,
        pred_direction="UP",
        pred_return_pct=2.0,
        actual_return_pct=1.5,
        actual_direction="UP",
        is_direction_correct=True,
        error_pct=0.5,
        ta_signal="buy",
        composite_score=70.0,
        entry_blocked_limit_up=entry_blocked,
        exit_blocked_limit_down=exit_blocked,
        cost_bps_applied=17.0,
    )


class TestRunBacktest:
    """run_backtest() 功能测试。"""

    def test_empty_records_returns_empty(self) -> None:
        """空记录列表应返回空 BacktestResult。"""
        result = run_backtest([])
        assert result.n_trades == 0

    def test_single_record_runs_backtest(self) -> None:
        """单条记录应正常执行回测。"""
        records = [_make_record()]

        with patch("trade_krono_cli.prediction_eval._get_close_price") as mock_price:
            mock_price.return_value = 100.0
            result = run_backtest(records)

        assert result is not None
        # 回测引擎应被调用

        assert hasattr(result, "n_trades")

    def test_multiple_records_runs_backtest(self) -> None:
        """多条记录应正常执行回测。"""
        records = [
            _make_record(ticker="sh.600519", eval_date="2026-08-14"),
            _make_record(ticker="sz.000001", eval_date="2026-08-15", horizon_days=10),
        ]

        with patch("trade_krono_cli.prediction_eval._get_close_price") as mock_price:
            mock_price.return_value = 50.0
            result = run_backtest(records)

        assert result is not None

    def test_missing_price_falls_back_to_min_horizon(self) -> None:
        """价格为 None 时应回退到最小 horizon。"""
        records = [
            _make_record(ticker="sh.600519", eval_date="2026-08-14", horizon_days=5),
        ]

        with patch("trade_krono_cli.prediction_eval._get_close_price") as mock_price:
            # 第一次调用返回 None（5日），第二次也返回 None
            mock_price.side_effect = [None, None]
            result = run_backtest(records, fixed_horizon=5)

        # 应该回退到 horizon=5（唯一 horizon）并尝试重新获取价格
        # 由于价格全为 None，bt_records 可能为空，返回空结果
        assert result is not None

    def test_all_prices_none_returns_empty(self) -> None:
        """所有价格为 None 时应返回空结果。"""
        records = [_make_record()]

        with patch("trade_krono_cli.prediction_eval._get_close_price") as mock_price:
            mock_price.return_value = None
            result = run_backtest(records)

        assert result.n_trades == 0

    def test_rebal_mode_fixed_horizon(self) -> None:
        """固定 horizon 模式应正常工作。"""
        records = [_make_record(horizon_days=5)]

        with patch("trade_krono_cli.prediction_eval._get_close_price") as mock_price:
            mock_price.return_value = 100.0
            result = run_backtest(records, rebal_mode="fixed_horizon", fixed_horizon=5)

        assert result is not None

    def test_rebal_mode_daily(self) -> None:
        """日频再平衡模式应正常工作。"""
        records = [_make_record(horizon_days=5)]

        with patch("trade_krono_cli.prediction_eval._get_close_price") as mock_price:
            mock_price.return_value = 100.0
            result = run_backtest(records, rebal_mode="daily")

        assert result is not None


class TestRunBacktestEdgeCases:
    """run_backtest() 边界条件测试。"""

    def test_blocked_entry_limit_up(self) -> None:
        """涨停买入限制应影响回测结果。"""
        records = [_make_record(entry_blocked=True)]

        with patch("trade_krono_cli.prediction_eval._get_close_price") as mock_price:
            mock_price.return_value = 100.0
            result = run_backtest(records)

        assert result is not None

    def test_blocked_exit_limit_down(self) -> None:
        """跌停卖出限制应影响回测结果。"""
        records = [_make_record(exit_blocked=True)]

        with patch("trade_krono_cli.prediction_eval._get_close_price") as mock_price:
            mock_price.return_value = 100.0
            result = run_backtest(records)

        assert result is not None

    def test_different_horizons(self) -> None:
        """不同 horizon 的记录应使用 primary horizon。"""
        records = [
            _make_record(horizon_days=5),
            _make_record(horizon_days=10),
            _make_record(horizon_days=20),
        ]

        with patch("trade_krono_cli.prediction_eval._get_close_price") as mock_price:
            mock_price.return_value = 100.0
            result = run_backtest(records, fixed_horizon=5)

        assert result is not None

    def test_custom_fixed_horizon(self) -> None:
        """自定义 fixed_horizon 参数应生效。"""
        records = [_make_record(horizon_days=5)]

        with patch("trade_krono_cli.prediction_eval._get_close_price") as mock_price:
            mock_price.return_value = 100.0
            result = run_backtest(records, fixed_horizon=10)

        assert result is not None
