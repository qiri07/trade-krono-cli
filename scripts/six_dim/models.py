"""six_dim 选股 — 数据模型。

包含 LogicMetrics（逻辑维度原始指标）和 StockScore（六维打分结果）两个数据类。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from scripts.six_dim.constants import DIM_WEIGHTS, TIER_WIDTH


@dataclass
class LogicMetrics:
    """逻辑维度的原始基本面指标（来自 buffett_cache.db）。"""

    code: str
    name: str
    roe_avg: float
    roe_min: float
    roe_excl_avg: float
    debt_ratio: float | None
    gross_margin: float | None
    profit_cagr: float
    revenue_cagr: float
    cfo_ratio: float | None
    latest_profit_yi: float
    latest_profit_yoy: float
    positive_years: int
    total_years: int
    is_financial: bool = False

    def to_payload(self) -> dict[str, Any]:
        """序列化为可写入 JSON 的字典。"""
        return {
            "code": self.code,
            "name": self.name,
            "roe_avg": round(self.roe_avg, 2),
            "roe_min": round(self.roe_min, 2),
            "roe_excl_avg": round(self.roe_excl_avg, 2),
            "debt_ratio": None if self.debt_ratio is None else round(self.debt_ratio, 1),
            "gross_margin": None if self.gross_margin is None else round(self.gross_margin, 1),
            "profit_cagr": round(self.profit_cagr, 2),
            "revenue_cagr": round(self.revenue_cagr, 2),
            "cfo_ratio": None if self.cfo_ratio is None else round(self.cfo_ratio, 2),
            "latest_profit_yi": round(self.latest_profit_yi, 2),
            "latest_profit_yoy": round(self.latest_profit_yoy, 2),
            "positive_years": self.positive_years,
            "total_years": self.total_years,
            "is_financial": self.is_financial,
        }


@dataclass
class StockScore:
    """单只股票的六维打分结果。"""

    code: str
    name: str
    ticker: str
    logic: float = 0.0
    valuation: float = 0.0
    position: float = 0.0
    trend: float = 0.0
    holder: float = 0.0
    vol_price: float = 0.0
    composite: float = 0.0
    metrics: dict[str, Any] = field(default_factory=dict)
    notes: dict[str, str] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        """序列化为可写入 JSON 的字典。"""
        payload: dict[str, Any] = {
            "code": self.code,
            "name": self.name,
            "ticker": self.ticker,
            "composite": round(self.composite, 2),
        }
        for key, _ in DIM_WEIGHTS:
            payload[key] = round(getattr(self, key), 1)
        payload["metrics"] = self.metrics
        payload["notes"] = self.notes
        return payload

    @property
    def tiered_key(self) -> tuple[float, ...]:
        """分档字典序排序键：六维各按 ``TIER_WIDTH`` 分一档，逐维比较。

        先比逻辑所在档，档相同再比估值档，依次到量价；六维全同档时用综合分兜底。
        这样「逻辑 80-85 分」始终排在「逻辑 75-80 分」之前，同档内再看估值，
        避免综合分加权把小维度的高分放大成主排序依据。
        """
        return (
            *(float(int(getattr(self, dim) // TIER_WIDTH)) for dim in (d for d, _ in DIM_WEIGHTS)),
            self.composite,
        )
