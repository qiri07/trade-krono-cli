"""
Stock — 领域层股票实体。

代表一只正在被分析的 A 股，携带标识信息和基础元数据。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, order=False)
class Stock:
    """
    一只 A 股的领域实体。

    Parameters
    ----------
    ticker        股票代码（如 "sh.600519"）
    name          股票名称（可选）
    industry      所属行业（可选）
    market_cap    市值（亿元，可选）
    pe_ratio      市盈率（可选）
    pb_ratio      市净率（可选）
    listed_date   上市日期 ISO 字符串（可选）
    """

    ticker: str
    name: str = ""
    industry: str = ""
    market_cap: float | None = None
    pe_ratio: float | None = None
    pb_ratio: float | None = None
    listed_date: str | None = None

    @property
    def code(self) -> str:
        """纯数字代码（去除交易所前缀）。"""
        return self.ticker.split(".", 1)[-1] if "." in self.ticker else self.ticker

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "name": self.name,
            "industry": self.industry,
            "market_cap": self.market_cap,
            "pe_ratio": self.pe_ratio,
            "pb_ratio": self.pb_ratio,
            "listed_date": self.listed_date,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Stock":
        return cls(
            ticker=data["ticker"],
            name=data.get("name", ""),
            industry=data.get("industry", ""),
            market_cap=data.get("market_cap"),
            pe_ratio=data.get("pe_ratio"),
            pb_ratio=data.get("pb_ratio"),
            listed_date=data.get("listed_date"),
        )

    def __lt__(self, other: "Stock") -> bool:
        return self.ticker < other.ticker

    def __hash__(self) -> int:
        return hash(self.ticker)
