"""Filter Rules Stage — 自定义规则链过滤。

将 FilterConfig.filter_rules 中的自定义规则应用到 UniverseTicket 列表，
作为基本面过滤之后的补充过滤层。

字段映射（UniverseTicket 字段别名）：
  market_cap_billion → market_cap  (同一概念，亿元)
  pe_ttm             → pe           (PE 动态 ≈ PE TTM)
  is_st              → 由 StaticStage 处理，此处不重复
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from trade_krono_cli.stock_filter import FilterOp, FilterRule
from trade_krono_cli.universe.stages import FilterStage

if TYPE_CHECKING:
    from collections.abc import Sequence

    from trade_krono_cli.universe.provider import UniverseTicket

# UniverseTicket 字段别名：规则中的常见字段名 → UniverseTicket 实际属性
_FIELD_ALIAS: dict[str, str] = {
    "market_cap_billion": "market_cap",
    "pe_ttm": "pe",
    "pe": "pe",
    "pb": "pb",
    "price": "price",
    "volume_ratio": "volume_ratio",
    "turnover_rate": "turnover_rate",
}


def _get_field(ticket: UniverseTicket, field: str) -> object:
    """根据字段名（含别名）从 UniverseTicket 获取值。"""
    attr = _FIELD_ALIAS.get(field, field)
    return getattr(ticket, attr, None)


class FilterRulesStage(FilterStage):
    """自定义规则过滤阶段。

    将 FilterConfig.filter_rules 中定义的规则逐一应用到每只股票，
    任何规则未通过即被排除。None 字段会跳过依赖该字段的规则。
    """

    name = "rules"

    def __init__(self, rules: Sequence[FilterRule] | None = None) -> None:
        self.rules: list[FilterRule] = list(rules) if rules is not None else []

    def filter(self, tickets: list[UniverseTicket]) -> list[UniverseTicket]:
        if not tickets or not self.rules:
            return tickets

        kept: list[UniverseTicket] = []
        rejected_count = 0

        for t in tickets:
            passed = True
            for rule in self.rules:
                value = _get_field(t, rule.field)
                if value is None:
                    # None 字段跳过依赖该字段的规则
                    continue
                if not _apply_rule(value, rule.op, rule.value):
                    rejected_count += 1
                    passed = False
                    break
            if passed:
                kept.append(t)

        if self.rules:
            logger.info(
                f"📋 Rules stage: {len(tickets)} → {len(kept)} "
                f"(应用 {len(self.rules)} 条规则，排除 {rejected_count} 只)",
            )
        return kept


def _apply_rule(value: object, op: FilterOp, rule_value: object) -> bool:
    """根据操作符对单条规则求值。"""
    try:
        if op == FilterOp.MIN:
            return value >= rule_value  # type: ignore[operator]
        if op == FilterOp.MAX:
            return value <= rule_value  # type: ignore[operator]
        if op == FilterOp.RANGE:
            range_val = rule_value  # type: ignore[misc]
            low = range_val[0]  # type: ignore[index]
            high = range_val[1]  # type: ignore[index]
            return low <= value <= high  # type: ignore[operator]
        if op == FilterOp.IN:
            return value in rule_value  # type: ignore[operator]
        if op == FilterOp.NOT_IN:
            return value not in rule_value  # type: ignore[operator]
        if op == FilterOp.CONTAINS:
            return str(rule_value) in str(value)
        if op == FilterOp.MATCH:
            import re

            return bool(re.search(str(rule_value), str(value)))
    except (TypeError, ValueError):
        return False
    return True
