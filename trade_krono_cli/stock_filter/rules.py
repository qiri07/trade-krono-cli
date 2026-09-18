"""Stock Filter Rules — 过滤规则定义（操作符 + 规则类）。

从 stock_filter.py 拆分，职责单一：
  · FilterOp 枚举（min/max/range/in/not_in/contains/match）
  · FilterRule 基类及所有具体规则实现

StockFilter 引擎和 StockMeta 数据类在 engine.py 中。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

# ── 操作符枚举 ────────────────────────────────────────────────────────────────


class FilterOp(str, Enum):
    """过滤操作符。"""

    # 范围类
    MIN = "min"  # ≥ value（字段值 >= 下限）
    MAX = "max"  # ≤ value（字段值 <= 上限）
    RANGE = "range"  # [low, high]
    # 集合类
    IN = "in"  # 在白名单中
    NOT_IN = "not_in"  # 不在黑名单中
    # 子串类
    CONTAINS = "contains"  # 字段包含子串（行业名模糊匹配）
    # 正则类
    MATCH = "match"  # 字段匹配正则表达式


# ── 规则基类与具体规则 ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FilterRule:
    """单条过滤规则（不可变）。"""

    field: str  # 字段名（在 StockMeta 中）
    op: FilterOp
    value: object  # 比较值（float / set / str / tuple）
    label: str = ""  # 人类可读描述（用于日志）

    def __post_init__(self) -> None:
        if not self.label:
            object.__setattr__(self, "label", f"{self.field} {self.op.value}")


class MinValueRule(FilterRule):
    """字段值 >= value。"""

    def __init__(self, field: str, value: float, label: str = "") -> None:
        super().__init__(field=field, op=FilterOp.MIN, value=value, label=label or f">={value}")


class MaxValueRule(FilterRule):
    """字段值 <= value。"""

    def __init__(self, field: str, value: float, label: str = "") -> None:
        super().__init__(field=field, op=FilterOp.MAX, value=value, label=label or f"<={value}")


class RangeRule(FilterRule):
    """字段值在 [low, high] 范围内。"""

    def __init__(self, field: str, low: float, high: float, label: str = "") -> None:
        super().__init__(
            field=field,
            op=FilterOp.RANGE,
            value=(low, high),
            label=label or f"[{low}, {high}]",
        )


class InSetRule(FilterRule):
    """字段值在集合中（白名单）。"""

    def __init__(self, field: str, values: set, label: str = "") -> None:
        super().__init__(
            field=field,
            op=FilterOp.IN,
            value=frozenset(values),
            label=label or f"IN {values}",
        )


class NotInSetRule(FilterRule):
    """字段值不在集合中（黑名单）。"""

    def __init__(self, field: str, values: set, label: str = "") -> None:
        super().__init__(
            field=field,
            op=FilterOp.NOT_IN,
            value=frozenset(values),
            label=label or f"NOT_IN {values}",
        )


class ContainsRule(FilterRule):
    """字段包含指定子串。"""

    def __init__(self, field: str, substr: str, label: str = "") -> None:
        super().__init__(
            field=field,
            op=FilterOp.CONTAINS,
            value=substr,
            label=label or f"contains '{substr}'",
        )


class MatchRule(FilterRule):
    """字段匹配正则表达式。"""

    def __init__(self, field: str, pattern: str, label: str = "") -> None:
        super().__init__(
            field=field,
            op=FilterOp.MATCH,
            value=re.compile(pattern),
            label=label or f"matches '{pattern}'",
        )
