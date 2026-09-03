"""股票过滤配置。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trade_krono_cli.stock_filter import FilterRule


@dataclass(frozen=False)
class FilterConfig:
    """股票过滤参数。"""

    min_confidence: float = 55.0
    allowed_signals: tuple[str, ...] = field(default=("BUY", "OVERWEIGHT", "HOLD"))

    # ── 前置市场范围过滤（UniverseEngine）────────────────
    exclude_st: bool = True
    """是否排除 ST / *ST 股票。"""
    exclude_low_price: bool = True
    """是否排除低价股（股价低于阈值）。"""
    low_price_threshold: float = 3.0
    """低价股阈值（元），低于此价的股票被排除。"""
    min_pb: float | None = None
    """最低市净率，PB 低于此值视为资不抵债风险，默认不过滤。"""

    # ── 基本面过滤 ────────────────────────────────────────
    market_cap_range: tuple[float, float] | None = None
    """市值范围（亿元），格式：(min, max)，为 None 时不过滤。"""
    market_cap_min: float | None = None
    """市值最小值（亿元），低于此值排除。"""
    industry_whitelist: list[str] = field(default_factory=list)
    industry_blacklist: list[str] = field(default_factory=list)
    pe_range: tuple[float, float] | None = None
    pb_range: tuple[float, float] | None = None
    max_risk_score: float | None = None
    min_volume_ratio: float | None = None
    min_turnover_rate: float | None = None
    min_volume: float | None = None
    """最小成交量（手），低于此值排除。"""

    # ── 自定义规则（应用于 Universe 和 StockFilter）────────
    filter_rules: list[FilterRule] = field(default_factory=list)

    universe_source: str = "akshare"
    """全市场数据源：akshare / baostock / mootdx / tushare。"""

    def merge(self, **overrides) -> FilterConfig:
        current = {k: getattr(self, k) for k in self.__dataclass_fields__}
        current.update(overrides)
        return FilterConfig(**current)

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not (0 <= self.min_confidence <= 100):
            errors.append(f"filter.min_confidence={self.min_confidence} 必须在 [0, 100] 范围内")
        if not self.allowed_signals:
            errors.append("filter.allowed_signals 不能为空")
        if self.exclude_low_price and self.low_price_threshold <= 0:
            errors.append(f"filter.low_price_threshold={self.low_price_threshold} 必须 > 0")
        if self.min_pb is not None and self.min_pb < 0:
            errors.append(f"filter.min_pb={self.min_pb} 必须 >= 0")
        return errors
