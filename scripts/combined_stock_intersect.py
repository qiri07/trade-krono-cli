#!/usr/bin/env python3
"""combined_stock_analysis 交集计算模块。

包含 build_intersection 函数，负责从各选股线来源集合中
计算命中数 >= min_sources 的股票交集，并按命中条数降序排序。
"""

from __future__ import annotations

from collections import Counter

from scripts.combined_stock_models import StockCandidate


def build_intersection(
    source_sets: dict[str, set[str]], min_sources: int
) -> list[StockCandidate]:
    """按命中条数过滤并排序，返回交集候选池。

    排序规则：命中条数降序。
    """
    counter: Counter[str] = Counter()
    for codes in source_sets.values():
        for c in codes:
            counter[c] += 1

    cands = [StockCandidate(code=c, hit_count=n) for c, n in counter.items() if n >= min_sources]

    # 标注每条命中来源（保留展示顺序）
    src_by_code: dict[str, list[str]] = {}
    for src, codes in source_sets.items():
        for c in codes:
            src_by_code.setdefault(c, []).append(src)
    for cand in cands:
        cand.sources = src_by_code.get(cand.code, [])

    cands.sort(key=lambda x: -x.hit_count)
    return cands
