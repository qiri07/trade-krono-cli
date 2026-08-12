"""
scorer — 打分逻辑。

从 merge.py 细化，专注于综合打分函数。
"""
from __future__ import annotations

from typing import Callable, Optional

from trade_krono_cli.merge import default_scorer, merge_results, run_risk_assessment


def score_merged_results(
    merged_items: list[dict],
    scorer: Optional[Callable] = None,
) -> list[dict]:
    """
    对已合并的结果重新打分并排序。

    这是 merge_results 的补充，用于在约束检查后重新评分。

    Parameters
    ----------
    merged_items : merge_results 输出的原始列表（尚未排序）
    scorer : 自定义打分函数

    Returns
    -------
    排序后的结果列表
    """
    if scorer is None:
        scorer = default_scorer

    for item in merged_items:
        item["composite_score"] = scorer(item)

    merged_items.sort(key=lambda x: (x.get("composite_score") or 0), reverse=True)

    for i, item in enumerate(merged_items, 1):
        item["rank"] = i

    return merged_items
