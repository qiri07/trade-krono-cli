"""测试 eval_ta.py — TA 信号胜率评估逻辑。

覆盖：compute_ta_metrics 的 BUY 胜率、HOLD 平均收益、空记录边界。
"""

from __future__ import annotations

from trade_krono_cli.eval_data import EvalRecord, HorizonMetrics
from trade_krono_cli.eval_ta import compute_ta_metrics


def _make_record(
    ticker: str,
    ta_signal: str,
    actual_return_pct: float,
    eval_date: str = "2026-08-11",
    horizon_days: int = 5,
) -> EvalRecord:
    return EvalRecord(
        ticker=ticker,
        eval_date=eval_date,
        horizon_days=horizon_days,
        pred_direction="UP",
        pred_return_pct=1.0,
        actual_return_pct=actual_return_pct,
        actual_direction="UP" if actual_return_pct > 0 else "DOWN",
        is_direction_correct=actual_return_pct > 0,
        error_pct=0.0,
        ta_signal=ta_signal,
        composite_score=50.0,
    )


# ═══════════════════════════════════════════════════════
# compute_ta_metrics — BUY 胜率
# ═══════════════════════════════════════════════════════


def test_compute_ta_metrics_buy_win_rate_all_wins():
    """所有 BUY 皆盈利：胜率 100%。"""
    records = [
        _make_record("sh.600519", "BUY", 5.0),
        _make_record("sh.600519", "BUY", 3.0),
        _make_record("sh.600519", "BUY", 1.0),
    ]
    m = HorizonMetrics()
    buy_n, hold_n = compute_ta_metrics(records, m)
    assert buy_n == 3
    assert hold_n == 0
    assert m.ta_buy_win_rate == 100.0
    assert m.ta_buy_avg_return == round((5.0 + 3.0 + 1.0) / 3, 2)


def test_compute_ta_metrics_buy_win_rate_mixed():
    """胜负混合：胜率 = 胜 / 总数。"""
    records = [
        _make_record("sh.600519", "BUY", 5.0),   # 胜
        _make_record("sh.600519", "BUY", -2.0),  # 负
        _make_record("sh.600519", "BUY", 0.0),   # 不大于 0，不算胜
        _make_record("sh.600519", "BUY", 3.0),   # 胜
    ]
    m = HorizonMetrics()
    buy_n, _ = compute_ta_metrics(records, m)
    assert buy_n == 4
    assert m.ta_buy_win_rate == 50.0  # 2 胜 / 4 = 50%
    assert m.ta_buy_avg_return == round((5.0 - 2.0 + 0.0 + 3.0) / 4, 2)


def test_compute_ta_metrics_buy_win_rate_all_losses():
    """全部亏损：胜率 0%。"""
    records = [
        _make_record("sh.600519", "BUY", -1.0),
        _make_record("sh.600519", "BUY", -5.0),
    ]
    m = HorizonMetrics()
    buy_n, _ = compute_ta_metrics(records, m)
    assert buy_n == 2
    assert m.ta_buy_win_rate == 0.0


# ═══════════════════════════════════════════════════════
# compute_ta_metrics — HOLD 平均收益
# ═══════════════════════════════════════════════════════


def test_compute_ta_metrics_hold_avg_return():
    """HOLD 记录：计算平均收益。"""
    records = [
        _make_record("sh.600519", "HOLD", 2.0),
        _make_record("sh.600519", "HOLD", 4.0),
        _make_record("sh.600519", "HOLD", 6.0),
    ]
    m = HorizonMetrics()
    _, hold_n = compute_ta_metrics(records, m)
    assert hold_n == 3
    assert m.ta_hold_avg_return == 4.0


def test_compute_ta_metrics_hold_negative_avg():
    """HOLD 平均收益为负。"""
    records = [
        _make_record("sh.600519", "HOLD", -3.0),
        _make_record("sh.600519", "HOLD", -1.0),
    ]
    m = HorizonMetrics()
    _, hold_n = compute_ta_metrics(records, m)
    assert hold_n == 2
    assert m.ta_hold_avg_return == -2.0


# ═══════════════════════════════════════════════════════
# compute_ta_metrics — 边界情况
# ═══════════════════════════════════════════════════════


def test_compute_ta_metrics_empty_records():
    """空记录：不写入任何指标，返回 (0, 0)。"""
    m = HorizonMetrics()
    buy_n, hold_n = compute_ta_metrics([], m)
    assert buy_n == 0
    assert hold_n == 0
    assert m.ta_buy_win_rate == 0.0
    assert m.ta_buy_avg_return == 0.0
    assert m.ta_hold_avg_return == 0.0


def test_compute_ta_metrics_no_buy_records():
    """仅 HOLD 记录：BUY 指标保持默认 0。"""
    records = [
        _make_record("sh.600519", "HOLD", 1.0),
        _make_record("sh.600519", "HOLD", 2.0),
    ]
    m = HorizonMetrics()
    buy_n, hold_n = compute_ta_metrics(records, m)
    assert buy_n == 0
    assert hold_n == 2
    assert m.ta_buy_win_rate == 0.0
    assert m.ta_buy_avg_return == 0.0
    assert m.ta_hold_avg_return == 1.5


def test_compute_ta_metrics_no_hold_records():
    """仅 BUY 记录：HOLD 指标保持默认 0。"""
    records = [
        _make_record("sh.600519", "BUY", 3.0),
        _make_record("sh.600519", "BUY", -1.0),
    ]
    m = HorizonMetrics()
    buy_n, hold_n = compute_ta_metrics(records, m)
    assert buy_n == 2
    assert hold_n == 0
    assert m.ta_hold_avg_return == 0.0
    assert m.ta_buy_win_rate == 50.0


def test_compute_ta_metrics_unknown_signal_ignored():
    """非 BUY/HOLD 信号（如 SELL）：被忽略，不计入任何统计。"""
    records = [
        _make_record("sh.600519", "SELL", 5.0),
        _make_record("sh.600519", "BUY", 3.0),
    ]
    m = HorizonMetrics()
    buy_n, hold_n = compute_ta_metrics(records, m)
    assert buy_n == 1
    assert hold_n == 0
    assert m.ta_buy_win_rate == 100.0


def test_compute_ta_metrics_return_values():
    """返回 (ta_buy_n, ta_hold_n) 正确。"""
    records = [
        _make_record("sh.600519", "BUY", 1.0),
        _make_record("sh.600519", "BUY", 2.0),
        _make_record("sh.600519", "HOLD", 3.0),
        _make_record("sh.600519", "HOLD", 4.0),
        _make_record("sh.600519", "HOLD", 5.0),
    ]
    m = HorizonMetrics()
    buy_n, hold_n = compute_ta_metrics(records, m)
    assert (buy_n, hold_n) == (2, 3)
