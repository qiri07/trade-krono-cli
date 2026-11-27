"""测试 eval_data.py — 预测评估数据层。

覆盖：calc_return / apply_roundtrip_cost / is_price_at_limit，
以及 EvalRecord / HorizonMetrics / BacktestResult / EvaluationSummary 数据类。
"""

from __future__ import annotations

from trade_krono_cli.eval_data import (
    BacktestResult,
    EvalRecord,
    EvaluationSummary,
    HorizonMetrics,
    apply_roundtrip_cost,
    calc_return,
    is_price_at_limit,
)

# ═══════════════════════════════════════════════════════
# calc_return
# ═══════════════════════════════════════════════════════


def test_calc_return_normal_gain():
    """正常上涨：收益率为正。"""
    assert calc_return(100.0, 110.0) == 10.0


def test_calc_return_normal_loss():
    """正常下跌：收益率为负。"""
    assert calc_return(100.0, 90.0) == -10.0


def test_calc_return_zero_entry_price():
    """入场价为 0：返回 0.0（避免除零）。"""
    assert calc_return(0.0, 110.0) == 0.0


def test_calc_return_zero_exit_price():
    """退出价为 0：返回 0.0。"""
    assert calc_return(100.0, 0.0) == 0.0


def test_calc_return_negative_prices():
    """负价格：返回 0.0。"""
    assert calc_return(-100.0, 110.0) == 0.0
    assert calc_return(100.0, -110.0) == 0.0


# ═══════════════════════════════════════════════════════
# apply_roundtrip_cost
# ═══════════════════════════════════════════════════════


def test_apply_roundtrip_cost_default_bps():
    """默认 17 bps：扣除 0.17%。"""
    assert apply_roundtrip_cost(10.0) == 9.83


def test_apply_roundtrip_cost_custom_bps():
    """自定义 bps：扣除相应成本。"""
    assert apply_roundtrip_cost(10.0, cost_bps=50.0) == 9.5


def test_apply_roundtrip_cost_zero_bps():
    """0 bps：不扣除成本。"""
    assert apply_roundtrip_cost(10.0, cost_bps=0.0) == 10.0


def test_apply_roundtrip_cost_negative_return():
    """负收益：继续扣除成本，结果更负。"""
    assert apply_roundtrip_cost(-5.0) == -5.17


# ═══════════════════════════════════════════════════════
# is_price_at_limit
# ═══════════════════════════════════════════════════════


def test_is_price_at_limit_up_hits_limit():
    """涨停：价格 >= limit_up * 0.999 返回 True。"""
    # prev_close=100，涨停幅度 10% → limit_up=110，阈值 109.89
    assert is_price_at_limit("sh.600519", 110.0, 100.0, "up") is True
    assert is_price_at_limit("sh.600519", 109.9, 100.0, "up") is True


def test_is_price_at_limit_up_not_hit():
    """未涨停：价格 < limit_up * 0.999 返回 False。"""
    assert is_price_at_limit("sh.600519", 105.0, 100.0, "up") is False


def test_is_price_at_limit_down_hits_limit():
    """跌停：price / limit_down <= 1.001 返回 True。"""
    # prev_close=100，跌停幅度 10% → limit_down=90，90/90=1.0 <= 1.001
    assert is_price_at_limit("sh.600519", 90.0, 100.0, "down") is True


def test_is_price_at_limit_down_not_hit():
    """未跌停：返回 False。"""
    assert is_price_at_limit("sh.600519", 95.0, 100.0, "down") is False


def test_is_price_at_limit_invalid_prev_close():
    """prev_close 为 None 或 <=0：返回 False。"""
    assert is_price_at_limit("sh.600519", 110.0, None, "up") is False
    assert is_price_at_limit("sh.600519", 110.0, 0.0, "up") is False
    assert is_price_at_limit("sh.600519", 110.0, -10.0, "up") is False


# ═══════════════════════════════════════════════════════
# EvalRecord
# ═══════════════════════════════════════════════════════


def test_eval_record_defaults():
    """EvalRecord 可选字段默认值。"""
    rec = EvalRecord(
        ticker="sh.600519",
        eval_date="2026-08-11",
        horizon_days=5,
        pred_direction="UP",
        pred_return_pct=1.0,
        actual_return_pct=1.5,
        actual_direction="UP",
        is_direction_correct=True,
        error_pct=-0.5,
    )
    assert rec.p10 is None
    assert rec.p50 is None
    assert rec.p90 is None
    assert rec.ta_signal is None
    assert rec.composite_score is None
    assert rec.entry_blocked_limit_up is False
    assert rec.exit_blocked_limit_down is False
    assert rec.cost_bps_applied == 0.0


def test_eval_record_all_fields():
    """EvalRecord 所有字段赋值。"""
    rec = EvalRecord(
        ticker="sz.000858",
        eval_date="2026-08-12",
        horizon_days=10,
        pred_direction="DOWN",
        pred_return_pct=-2.0,
        actual_return_pct=-1.5,
        actual_direction="DOWN",
        is_direction_correct=True,
        error_pct=-0.5,
        p10=-3.0,
        p25=-2.5,
        p50=-2.0,
        p75=-1.5,
        p90=-1.0,
        ta_signal="SELL",
        composite_score=30.0,
        entry_blocked_limit_up=True,
        exit_blocked_limit_down=False,
        cost_bps_applied=17.0,
    )
    assert rec.p10 == -3.0
    assert rec.p90 == -1.0
    assert rec.ta_signal == "SELL"
    assert rec.composite_score == 30.0
    assert rec.entry_blocked_limit_up is True
    assert rec.cost_bps_applied == 17.0


# ═══════════════════════════════════════════════════════
# HorizonMetrics
# ═══════════════════════════════════════════════════════


def test_horizon_metrics_defaults():
    """HorizonMetrics 所有字段默认为 0.0。"""
    m = HorizonMetrics()
    assert m.kronos_dir_accuracy == 0.0
    assert m.ta_buy_win_rate == 0.0
    assert m.ta_buy_avg_return == 0.0
    assert m.ta_hold_avg_return == 0.0
    assert m.combined_buy_up_win_rate == 0.0
    assert m.combined_buy_up_avg_return == 0.0
    assert m.high_conf_win_rate == 0.0
    assert m.high_conf_avg_return == 0.0
    assert m.win_rate_pct == 0.0
    assert m.avg_return_pct == 0.0
    assert m.profit_factor == 0.0
    assert m.max_drawdown_pct == 0.0
    assert m.sharpe_ratio == 0.0
    assert m.ic_composite_mean == 0.0
    assert m.ic_composite_std == 0.0
    assert m.ic_composite_ir == 0.0
    assert m.rank_ic_composite_mean == 0.0
    assert m.ic_positive_pct == 0.0
    assert m.sortino_ratio == 0.0
    assert m.calmar_ratio == 0.0
    assert m.turnover == 0.0
    assert m.alpha_vs_benchmark == 0.0
    assert m.benchmark_return_pct == 0.0


def test_horizon_metrics_assignment():
    """HorizonMetrics 字段可赋值。"""
    m = HorizonMetrics(
        kronos_dir_accuracy=75.0,
        ta_buy_win_rate=60.0,
        ta_buy_avg_return=2.5,
        win_rate_pct=55.0,
        avg_return_pct=3.0,
        ic_composite_mean=0.04,
        ic_composite_ir=0.8,
        ic_positive_pct=70.0,
    )
    assert m.kronos_dir_accuracy == 75.0
    assert m.ta_buy_win_rate == 60.0
    assert m.ic_composite_mean == 0.04
    assert m.ic_positive_pct == 70.0


# ═══════════════════════════════════════════════════════
# BacktestResult
# ═══════════════════════════════════════════════════════


def test_backtest_result_defaults():
    """BacktestResult 默认值。"""
    b = BacktestResult()
    assert b.initial_capital == 1_000_000.0
    assert b.final_value == 0.0
    assert b.total_return_pct == 0.0
    assert b.metrics == {}
    assert b.equity_curve == []
    assert b.n_trades == 0
    assert b.rebal_mode == "fixed_horizon"
    assert b.records == []


def test_backtest_result_empty_static():
    """BacktestResult.empty() 返回空实例。"""
    b = BacktestResult.empty()
    assert b.final_value == 0.0
    assert b.n_trades == 0


def test_backtest_result_custom():
    """BacktestResult 自定义字段。"""
    b = BacktestResult(
        initial_capital=500_000.0,
        final_value=550_000.0,
        total_return_pct=10.0,
        n_trades=5,
        rebal_mode="rolling",
        records=[{"ticker": "sh.600519"}],
    )
    assert b.initial_capital == 500_000.0
    assert b.total_return_pct == 10.0
    assert b.n_trades == 5
    assert b.rebal_mode == "rolling"
    assert len(b.records) == 1


# ═══════════════════════════════════════════════════════
# EvaluationSummary
# ═══════════════════════════════════════════════════════


def test_evaluation_summary_defaults():
    """EvaluationSummary 默认值。"""
    s = EvaluationSummary()
    assert s.kronos_n == 0
    assert s.ta_buy_n == 0
    assert s.ta_hold_n == 0
    assert s.combined_buy_up_n == 0
    assert s.high_conf_n == 0
    assert s.entry_limit_up_blocked == 0
    assert s.exit_limit_down_blocked == 0
    assert s.cost_applied_n == 0
    assert s.horizons == {}
    assert s.records == []
    assert s.backtest is None
    assert s.benchmark_cum_return_pct == 0.0
    assert s.excess_return_pct == 0.0
    assert s.benchmark_curve == {}
    assert s.excess_curve == {}
    assert s.ic_composite_rank_mean == 0.0
    assert s.ic_composite_rank_ir == 0.0
    assert s.ic_kronos_rank_mean == 0.0
    assert s.ic_ta_rank_mean == 0.0
    assert s.alpha_best_benchmark == ""
    assert s.alpha_best_value == 0.0
    assert s.alpha_all == {}
    assert s.benchmark_results == {}


def test_evaluation_summary_with_records():
    """EvaluationSummary 含 records 和 horizons。"""
    rec = EvalRecord(
        ticker="sh.600519",
        eval_date="2026-08-11",
        horizon_days=5,
        pred_direction="UP",
        pred_return_pct=1.0,
        actual_return_pct=1.5,
        actual_direction="UP",
        is_direction_correct=True,
        error_pct=-0.5,
    )
    m = HorizonMetrics(kronos_dir_accuracy=80.0, ta_buy_win_rate=70.0)
    s = EvaluationSummary(
        kronos_n=1,
        ta_buy_n=1,
        records=[rec],
        horizons={5: m},
        backtest=BacktestResult.empty(),
        benchmark_cum_return_pct=5.0,
        excess_return_pct=2.0,
    )
    assert s.kronos_n == 1
    assert s.ta_buy_n == 1
    assert len(s.records) == 1
    assert s.records[0].ticker == "sh.600519"
    assert s.horizons[5].kronos_dir_accuracy == 80.0
    assert s.backtest is not None
    assert s.benchmark_cum_return_pct == 5.0
    assert s.excess_return_pct == 2.0
