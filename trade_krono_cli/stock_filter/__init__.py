"""Stock Filter — 股票过滤规则引擎。

职责：
  · 多条件规则链（置信度 / 信号 / 市值 / 行业 / PE / PB / 风险分 / 成交量）
  · 支持白名单 / 黑名单 / 范围 / 子串匹配等操作符
  · 从 baostock 批量获取过滤所需的元数据（PE / PB / 行业 / 市值）

使用方式：
    rules = [
        MinValueRule("ta_confidence", 55.0),
        InSetRule("signal", {"BUY", "HOLD"}),
        RangeRule("market_cap_billion", 50.0, 5000.0),
        SubstrRule("industry", "银行"),
        MaxValueRule("risk_score", 0.7),
    ]
    passed = StockFilter(rules).apply(stock_meta)
"""

from __future__ import annotations

# 向后兼容：从子模块重新导出所有公开 API
from trade_krono_cli.stock_filter.engine import (  # noqa: F401
    StockFilter,
    StockMeta,
    fetch_stock_meta,
)
from trade_krono_cli.stock_filter.rules import (  # noqa: F401
    ContainsRule,
    FilterOp,
    FilterRule,
    InSetRule,
    MatchRule,
    MaxValueRule,
    MinValueRule,
    NotInSetRule,
    RangeRule,
)

__all__ = [
    "FilterOp",
    "FilterRule",
    "MinValueRule",
    "MaxValueRule",
    "RangeRule",
    "InSetRule",
    "NotInSetRule",
    "ContainsRule",
    "MatchRule",
    "StockMeta",
    "StockFilter",
    "fetch_stock_meta",
]
