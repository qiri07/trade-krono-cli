"""股票过滤配置。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
from trade_krono_cli.stock_filter import FilterRule


@dataclass(frozen=False)
class FilterConfig:
    """股票过滤参数。"""

    min_confidence: float = 55.0
    allowed_signals: tuple[str, ...] = field(default=("BUY", "HOLD"))

    # ── 股票基本面过滤 ────────────────────────────────────────
    market_cap_range: Optional[tuple[float, float]] = None
    industry_whitelist: list[str] = field(default_factory=list)
    industry_blacklist: list[str] = field(default_factory=list)
    pe_range: Optional[tuple[float, float]] = None
    pb_range: Optional[tuple[float, float]] = None
    max_risk_score: Optional[float] = None
    min_volume_ratio: Optional[float] = None
    min_turnover_rate: Optional[float] = None
    exclude_st: bool = True
    filter_rules: list[FilterRule] = field(default_factory=list)
    universe_source: str = "akshare"
    """全市场数据源：akshare / baostock / mootdx / tushare。"""

    def merge(self, **overrides) -> "FilterConfig":
        current = {k: getattr(self, k) for k in self.__dataclass_fields__}
        current.update(overrides)
        return FilterConfig(**current)

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not (0 <= self.min_confidence <= 100):
            errors.append(
                f"filter.min_confidence={self.min_confidence} 必须在 [0, 100] 范围内"
            )
        if not self.allowed_signals:
            errors.append("filter.allowed_signals 不能为空")
        return errors
