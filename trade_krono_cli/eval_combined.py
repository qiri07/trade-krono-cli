"""
综合信号与高置信度评估。

负责统计 TA BUY + Kronos UP 组合信号，以及 composite_score ≥ 70 的高置信信号。
"""

from __future__ import annotations

from trade_krono_cli.eval_data import EvalRecord, HorizonMetrics


def compute_combined_metrics(
    h_records: list[EvalRecord],
    metrics: HorizonMetrics,
) -> int:
    """
    计算综合信号（TA BUY + Kronos UP）的胜率和平均收益。

    Returns
    -------
    综合信号记录数
    """
    combined = [r for r in h_records if r.ta_signal == "BUY" and r.pred_direction == "UP"]
    if not combined:
        return 0
    wins = sum(1 for r in combined if r.actual_return_pct > 0)
    avg_ret = sum(r.actual_return_pct for r in combined) / len(combined)
    metrics.combined_buy_up_win_rate = round(wins / len(combined) * 100, 1)
    metrics.combined_buy_up_avg_return = round(avg_ret, 2)
    return len(combined)


def compute_high_conf_metrics(
    h_records: list[EvalRecord],
    metrics: HorizonMetrics,
) -> int:
    """
    计算高置信信号（composite_score ≥ 70）的胜率和平均收益。

    Returns
    -------
    高置信信号记录数
    """
    high_conf = [r for r in h_records if r.composite_score is not None and r.composite_score >= 70]
    if not high_conf:
        return 0
    wins = sum(1 for r in high_conf if r.actual_return_pct > 0)
    avg_ret = sum(r.actual_return_pct for r in high_conf) / len(high_conf)
    metrics.high_conf_win_rate = round(wins / len(high_conf) * 100, 1)
    metrics.high_conf_avg_return = round(avg_ret, 2)
    return len(high_conf)
