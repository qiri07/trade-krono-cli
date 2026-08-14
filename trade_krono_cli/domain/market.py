"""
MarketSnapshot — 市场快照。

某只股票在某一时刻的完整市场状态，作为所有分析的输入事实。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as date_type
from typing import Optional

from trade_krono_cli.domain.stock import Stock


@dataclass(frozen=True)
class MarketSnapshot:
    """
    某只股票在某日的市场状态快照。

    此对象是 immutable 的事实记录：一旦创建，不再变更。
    所有下游分析（TA/Kronos/Risk）都以此快照为输入依据。

    Parameters
    ----------
    stock             股票实体
    date              分析日期（ISO 字符串）
    close             当日收盘价
    open              当日开盘价
    high              当日最高价
    low               当日最低价
    volume            当日成交量
    prev_close        前一日收盘价（用于计算涨跌幅）
    turnover_rate     换手率（可选）
    limit_up_price    涨停价（可选，来自 trading_constraints）
    limit_down_price  跌停价（可选）
    sector            所属行业（覆盖 Stock.industry，优先使用此处）
    extra             附加元数据（行业分类、指数权重等）
    """
    stock: Stock
    date: str
    close: float
    open: float
    high: float
    low: float
    volume: float
    prev_close: float
    turnover_rate: Optional[float] = None
    limit_up_price: Optional[float] = None
    limit_down_price: Optional[float] = None
    sector: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def change_pct(self) -> float:
        """当日涨跌幅（%）。"""
        if self.prev_close <= 0:
            return 0.0
        return (self.close - self.prev_close) / self.prev_close * 100.0

    @property
    def day_range_pct(self) -> float:
        """当日振幅（%）。"""
        if self.open <= 0:
            return 0.0
        return (self.high - self.low) / self.open * 100.0

    def contains_future(self, check_date: str) -> bool:
        """判断给定日期是否超出快照边界。"""
        return check_date > self.date

    def to_dict(self) -> dict:
        return {
            "stock": self.stock.to_dict(),
            "date": self.date,
            "close": self.close,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "volume": self.volume,
            "prev_close": self.prev_close,
            "change_pct": round(self.change_pct, 4),
            "turnover_rate": self.turnover_rate,
            "limit_up_price": self.limit_up_price,
            "limit_down_price": self.limit_down_price,
            "sector": self.sector or self.stock.industry,
            "extra": self.extra,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MarketSnapshot":
        stock_data = data.get("stock", {})
        stock = Stock.from_dict(stock_data) if isinstance(stock_data, dict) else Stock(ticker=data.get("ticker", ""))
        return cls(
            stock=stock,
            date=data["date"],
            close=float(data["close"]),
            open=float(data["open"]),
            high=float(data["high"]),
            low=float(data["low"]),
            volume=float(data["volume"]),
            prev_close=float(data["prev_close"]),
            turnover_rate=data.get("turnover_rate"),
            limit_up_price=data.get("limit_up_price"),
            limit_down_price=data.get("limit_down_price"),
            sector=data.get("sector", ""),
            extra=data.get("extra", {}),
        )
