"""trading_constraints.types — 交易约束结果数据结构。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class TradingConstraintResult:
    """单只股票交易约束检查的返回结果。"""

    symbol: str
    allowed: bool
    reason: str | None = None  # 拒绝原因（None 表示通过）
    cost_bps: float = 0.0  # 本次交易所需成本（bps）
    position_locked_until: date | None = None  # T+1 锁定到期日
    limit_up_price: float | None = None  # 今日涨停价
    limit_down_price: float | None = None  # 今日跌停价
    is_st: bool = False  # 是否为 ST 标的
