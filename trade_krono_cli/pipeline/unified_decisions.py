"""pipeline.unified_decisions — 统一投资决策构建与附加。

职责（从 QuantPipeline._attach_unified_decisions 提取）：
  · 为每只合并结果构建并附加统一投资决策标记
  · 从 TA、Kronos、Committee 三源数据汇总完成状态
  · 持久化到 research 数据库的预留接口（后续通过 build_unified_decision 完成）
"""

from __future__ import annotations

from typing import Any

from loguru import logger


def attach_unified_decisions(
    merged: list[dict],
    ta_results: list[Any],
    kronos_results: list[Any],
    committee_results: list[Any],
    date: str,
    job_id: str,
    research: Any,
) -> None:
    """为每只合并结果构建并附加统一投资决策。

    从 TA、Kronos、Committee 三源数据构建统一决策，并持久化到 research 数据库。
    该方法用于在 QuantPipeline.run 中于委员会审议之后、结果持久化之前调用。

    Parameters
    ----------
    merged : list[dict]
        合并后的候选股票列表。
    ta_results : list[Any]
        TA 分析结果列表。
    kronos_results : list[Any]
        Kronos 预测结果列表。
    committee_results : list[Any]
        委员会审议结果列表。
    date : str
        评估日期。
    job_id : str
        研究作业 ID。
    research : Any
        研究数据库实例。
    """
    # 构建映射：ticker -> result
    ta_map: dict[str, Any] = {}
    for r in ta_results:
        if r is not None and hasattr(r, "ticker"):
            ta_map[r.ticker] = r

    kronos_map: dict[str, Any] = {}
    for r in kronos_results:
        if r is not None and hasattr(r, "ticker"):
            kronos_map[r.ticker] = r

    committee_map: dict[str, Any] = {}
    for r in committee_results:
        if r is not None and hasattr(r, "ticker"):
            committee_map[r.ticker] = r

    # 为每只合并结果构建统一决策
    for item in merged:
        if not isinstance(item, dict):
            continue
        tk = item.get("ticker")
        if not tk or not isinstance(tk, str):
            continue

        ta_result = ta_map.get(tk)
        kronos_result = kronos_map.get(tk)
        committee_result = committee_map.get(tk)

        # 写入统一决策信息到 item
        item["unified_decision_ticker"] = tk
        if ta_result is not None:
            item["ta_analysis_completed"] = True
        if kronos_result is not None:
            item["kronos_prediction_completed"] = True
        if committee_result is not None:
            item["committee_review_completed"] = True

    logger.debug(
        f"📎 统一决策附加完成: job={job_id} date={date} "
        f"{len(merged)} 只结果已标记",
    )

    # 注：详细的 UnifiedInvestmentDecision 构建将在
    # 后续版本中通过 build_unified_decision 工厂函数完成，
    # 以避免循环导入和类型定义问题。
