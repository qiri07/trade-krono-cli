"""
TA 信号胜率评估。

负责统计 TA BUY / HOLD 信号的胜率和平均收益。
"""

from __future__ import annotations

from trade_krono_cli.eval_data import EvalRecord, HorizonMetrics


def compute_ta_metrics(
    h_records: list[EvalRecord],
    metrics: HorizonMetrics,
) -> tuple[int, int]:
    """
    计算 TA BUY 胜率和 HOLD 平均收益，更新 metrics。

    Returns
    -------
    (ta_buy_n, ta_hold_n)
    """
    buy_records = [r for r in h_records if r.ta_signal == "BUY"]
    ta_buy_n = 0
    ta_hold_n = 0

    if buy_records:
        wins = sum(1 for r in buy_records if r.actual_return_pct > 0)
        avg_ret = sum(r.actual_return_pct for r in buy_records) / len(buy_records)
        metrics.ta_buy_win_rate = round(wins / len(buy_records) * 100, 1)
        metrics.ta_buy_avg_return = round(avg_ret, 2)
        ta_buy_n = len(buy_records)

    hold_records = [r for r in h_records if r.ta_signal == "HOLD"]
    if hold_records:
        avg_ret = sum(r.actual_return_pct for r in hold_records) / len(hold_records)
        metrics.ta_hold_avg_return = round(avg_ret, 2)
        ta_hold_n = len(hold_records)

    return ta_buy_n, ta_hold_n
