"""
Universe Provider — 全市场股票列表获取接口。

定义 UniverseProvider ABC 以及基于 akshare 的实现。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

from loguru import logger

# ── 数据结构 ──────────────────────────────────────────────────────────────────

@dataclass
class UniverseTicket:
    """
    单只股票的宇宙票。

    来自数据源的原始数据尽量保留在此处，供后续各阶段消费。
    """
    ticker: str                # 归一化格式: sh.600519 / sz.000858
    name: str = ""             # 股票名称
    price: Optional[float] = None      # 最新价（元）
    pe: Optional[float] = None         # 市盈率（动态）
    pb: Optional[float] = None         # 市净率
    market_cap: Optional[float] = None # 总市值（亿元， akshare 返回单位）
    volume_ratio: Optional[float] = None  # 量比
    turnover_rate: Optional[float] = None # 换手率（%）
    source: str = ""                 # 数据来源标识


# ── 抽象基类 ──────────────────────────────────────────────────────────────────

class UniverseProvider(ABC):
    """
    全市场股票列表提供者。

    子类实现 get_universe() 方法，返回当前 A 股全市场股票列表。
    """

    name: str = "base"

    @abstractmethod
    def get_universe(self) -> list[UniverseTicket]:
        """
        获取 A 股全市场股票列表。

        Returns
        -------
        list[UniverseTicket]
            每只股票一条记录，ticker 已归一化为 sh./sz. 格式
        """
        ...

    def health_check(self) -> bool:
        try:
            tickets = self.get_universe()
            return len(tickets) > 0
        except Exception as e:
            logger.debug(f"{self.name} health check failed: {e}")
            return False


# ── AkShare 实现 ──────────────────────────────────────────────────────────────

class AkshareUniverseProvider(UniverseProvider):
    """
    基于 akshare 的 A 股市场范围提供者。

    使用 ak.stock_zh_a_spot_em() 获取全市场实时行情数据，
    包含 PE/PB/市值/量比/换手率 等字段，供后续阶段直接消费。
    """

    name = "akshare"

    def get_universe(self) -> list[UniverseTicket]:
        try:
            import akshare as ak
        except ImportError:
            logger.warning("akshare 未安装，无法获取全市场股票列表")
            return []

        try:
            df = ak.stock_zh_a_spot_em()
            if df is None or df.empty:
                logger.warning("akshare 返回空数据")
                return []

            tickets: list[UniverseTicket] = []
            for _, row in df.iterrows():
                code = str(row.get("代码", "")).strip()
                if not code or len(code) != 6:
                    continue

                ticker = self._code_to_ticker(code)
                name = str(row.get("名称", ""))

                tickets.append(UniverseTicket(
                    ticker=ticker,
                    name=name,
                    price=self._safe_float(row.get("最新价")),
                    pe=self._safe_float(row.get("市盈率-动态")),
                    pb=self._safe_float(row.get("市净率")),
                    market_cap=self._safe_float(row.get("总市值")),
                    volume_ratio=self._safe_float(row.get("量比")),
                    turnover_rate=self._safe_float(row.get("换手率")),
                    source=self.name,
                ))

            logger.info(
                f"📡 {self.name} 全市场获取: {len(tickets)} 只 A 股"
            )
            return tickets

        except Exception as e:
            logger.error(f"{self.name} 获取全市场失败: {e}")
            return []

    @staticmethod
    def _code_to_ticker(code: str) -> str:
        """6 位数字代码 → sh./sz. 归一化格式。"""
        if code.startswith(("6", "5", "9")):
            return f"sh.{code}"
        return f"sz.{code}"

    @staticmethod
    def _safe_float(value) -> Optional[float]:
        if value is None:
            return None
        try:
            f = float(value)
            if f != f or f == float("inf") or f == float("-inf"):
                return None
            return f
        except (ValueError, TypeError):
            return None


# ── 工厂函数 ──────────────────────────────────────────────────────────────────

_PROVIDER_REGISTRY: dict[str, type[UniverseProvider]] = {
    "akshare": AkshareUniverseProvider,
}


def get_universe_provider(source: str) -> Optional[UniverseProvider]:
    """
    根据 source 名称获取对应的 UniverseProvider 实例。

    Parameters
    ----------
    source : str
        数据源名称，如 "akshare"

    Returns
    -------
    UniverseProvider 或 None（不支持时）
    """
    cls = _PROVIDER_REGISTRY.get(source)
    if cls is None:
        logger.warning(f"不支持的 universe_source: {source}，使用默认 akshare")
        cls = _PROVIDER_REGISTRY.get("akshare")
    return cls() if cls else None
