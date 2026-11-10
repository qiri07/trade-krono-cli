#!/usr/bin/env python3
"""动态仓位管理模块。

基于 RD-Agent-Work/engine/position_manager.py 移植，适配 trade-krono-cli 回测流水线。

根据回撤状态和信号质量，动态调整每只股票的仓位权重。

策略：
  1. 根据回撤控制模块的 exposure 缩放总仓位
  2. 根据 composite_score 的排名质量分配个股权重
     - Top 3 股票分配更高权重（30%）
     - 中间排名分配中等权重（20%）
     - 尾部排名分配较低权重（10%）
  3. 结合回撤 exposure 给出最终 allocation
"""

from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger(__name__)


class DynamicPositionManager:
    """动态仓位管理器。

    用法：
        manager = DynamicPositionManager(top_n=3, top_weight=0.30, mid_weight=0.20, tail_weight=0.10)
        allocations = manager.compute_allocations(
            stocks=['sh.600519', 'sz.000001', ...],
            scores={'sh.600519': 2.5, 'sz.000001': 1.8, ...},
            exposure=0.60  # 来自 MaxDrawdownControl
        )
        # 返回: {'sh.600519': 0.18, 'sz.000001': 0.12, ...}  (权重总和 = exposure)
    """

    def __init__(
        self,
        top_n: int = 3,
        top_weight: float = 0.30,
        mid_weight: float = 0.20,
        tail_weight: float = 0.10,
        min_position_pct: float = 0.02,
    ) -> None:
        """初始化动态仓位管理器。

        Parameters
        ----------
        top_n : int
            头部股票数量。
        top_weight : float
            头部股票单只权重（总和 = top_n * top_weight）。
        mid_weight : float
            中间股票单只权重。
        tail_weight : float
            尾部股票单只权重。
        min_position_pct : float
            最小仓位比例（低于此值的股票不买入）。
        """
        self.top_n = top_n
        self.top_weight = top_weight
        self.mid_weight = mid_weight
        self.tail_weight = tail_weight
        self.min_position_pct = min_position_pct

    def compute_allocations(
        self,
        stocks: list[str],
        scores: dict[str, float],
        exposure: float,
    ) -> dict[str, float]:
        """计算每只股票的分配比例。

        Parameters
        ----------
        stocks : list[str]
            候选股票列表。
        scores : dict[str, float]
            {股票代码: 合成得分}。
        exposure : float
            当前总仓位比例 [0.0, 1.0]，通常来自 MaxDrawdownControl。

        Returns
        -------
        dict[str, float]
            {股票代码: 分配比例}，所有 allocation 之和 = exposure。
        """
        if not stocks or exposure <= 0:
            return {}

        # 按得分排序
        sorted_stocks = sorted(
            [(s, scores.get(s, 0.0)) for s in stocks],
            key=lambda x: x[1],
            reverse=True,
        )

        n = len(sorted_stocks)
        allocations: dict[str, float] = {}

        for rank_idx, (stock, score) in enumerate(sorted_stocks):
            if rank_idx < self.top_n:
                base_weight = self.top_weight
            elif rank_idx < n * 0.5:
                base_weight = self.mid_weight
            else:
                base_weight = self.tail_weight

            # 根据得分质量微调权重（得分越高，权重略高）
            if scores:
                score_values = list(scores.values())
                score_range = max(score_values) - min(score_values)
                score_norm = (
                    (score - min(score_values)) / score_range if score_range != 0 else 0.0
                )
                adjusted_weight = base_weight * (0.7 + 0.3 * score_norm)
            else:
                adjusted_weight = base_weight

            allocations[stock] = adjusted_weight * exposure

        # 归一化使总和 = exposure
        total = sum(allocations.values())
        if total > 0:
            for stock in allocations:
                allocations[stock] = allocations[stock] / total * exposure
        else:
            # 等权分配
            equal = exposure / n
            for stock in allocations:
                allocations[stock] = equal

        # 过滤最小仓位
        allocations = {
            s: a for s, a in allocations.items() if a >= self.min_position_pct
        }

        # 再次归一化
        total = sum(allocations.values())
        if total > 0 and abs(total - exposure) > 1e-6:
            for s in allocations:
                allocations[s] = allocations[s] / total * exposure

        return allocations

    def get_allocation_summary(self, allocations: dict[str, float]) -> dict:
        """获取分配摘要。

        Parameters
        ----------
        allocations : dict[str, float]
            {股票代码: 分配比例}。

        Returns
        -------
        dict
            包含 n_stocks, total_exposure, max_allocation, min_allocation,
            avg_allocation 等统计信息。
        """
        if not allocations:
            return {"n_stocks": 0, "total_exposure": 0.0}

        values = list(allocations.values())
        return {
            "n_stocks": len(values),
            "total_exposure": sum(values),
            "max_allocation": max(values),
            "min_allocation": min(values),
            "avg_allocation": float(np.mean(values)),
        }
