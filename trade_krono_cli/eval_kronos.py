"""
Kronos 方向准确率评估。

负责统计各 horizon 下 Kronos 预测方向（UP/DOWN/FLAT）与实际方向的一致性。
"""
from __future__ import annotations

from typing import Optional

from trade_krono_cli.eval_data import EvalRecord, HorizonMetrics


def compute_kronos_accuracy(
    h_records: list[EvalRecord],
    metrics: HorizonMetrics,
) -> int:
    """
    计算 Kronos 方向准确率并更新 metrics。

    Returns
    -------
    纳入统计的记录数（pred_direction 不为 None 的记录数）
    """
    kronos_records = [r for r in h_records if r.pred_direction is not None]
    if not kronos_records:
        return 0
    correct = sum(1 for r in kronos_records if r.is_direction_correct)
    acc = correct / len(kronos_records) * 100
    metrics.kronos_dir_accuracy = round(acc, 1)
    return len(kronos_records)
