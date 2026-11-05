#!/usr/bin/env python3
"""combined_stock_analysis 交集计算模块。

包含 build_intersection 函数，负责从各选股线来源集合中
计算命中数 >= min_sources 的股票交集，并按命中条数降序排序。
"""

from __future__ import annotations

from scripts.combined_stock_models import StockCandidate


def build_intersection(
    source_sets: dict[str, set[str]],
    min_sources: int,
) -> list[StockCandidate]:
    """按命中条数过滤并排序，返回交集候选池。

    Args:
        source_sets: {来源键: 6 位代码集合}，来自 collect_source_sets()。
        min_sources: 最少命中条数（股票需命中 >= min_sources 条线才入选）。

    Returns:
        StockCandidate 列表，sources 记录命中的来源键，
        按命中条数降序排序（同条数按代码升序保证确定性）。
    """
    hits: dict[str, list[str]] = {}
    for key, codes in source_sets.items():
        for code in codes:
            hits.setdefault(code, []).append(key)

    cands: list[StockCandidate] = []
    for code, sources in hits.items():
        if len(sources) >= min_sources:
            cands.append(
                StockCandidate(code=code, hit_count=len(sources), sources=sorted(sources))
            )

    cands.sort(key=lambda c: (-c.hit_count, c.code))
    return cands
