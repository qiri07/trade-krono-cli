"""six_dim 选股 — 排序逻辑。

实现「分档字典序」排序：六维各按 TIER_WIDTH 分档，先比逻辑所在档，
档内再比估值档，依次到量价；六维全同档时才用加权综合分兜底。
"""

from __future__ import annotations

from scripts.six_dim.models import StockScore


def sort_scores(results: list[StockScore]) -> list[StockScore]:
    """按分档字典序就地排序（逻辑 > 估值 > 位置 > 趋势 > 股东 > 量价）。

    Args:
        results: 待排序候选列表。

    Returns:
        排序后的同一列表对象（便于链式调用 / 测试断言）。
    """
    results.sort(key=lambda s: s.tiered_key, reverse=True)
    return results
