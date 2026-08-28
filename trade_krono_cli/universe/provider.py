"""
Universe Provider — 全市场股票列表获取接口。

定义 UniverseProvider ABC 以及基于 akshare / mootdx 的实现。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from loguru import logger

# ── 数据结构 ──────────────────────────────────────────────────────────────────


@dataclass
class UniverseTicket:
    """
    单只股票的宇宙票。

    来自数据源的原始数据尽量保留在此处，供后续各阶段消费。
    """

    ticker: str  # 归一化格式: sh.600519 / sz.000858
    name: str = ""  # 股票名称
    price: Optional[float] = None  # 最新价（元）
    pe: Optional[float] = None  # 市盈率（动态）
    pb: Optional[float] = None  # 市净率
    market_cap: Optional[float] = None  # 总市值（亿元， akshare 返回单位）
    volume_ratio: Optional[float] = None  # 量比
    turnover_rate: Optional[float] = None  # 换手率（%）
    industry: Optional[str] = None  # 行业名称（如 "银行"、"食品饮料"）
    source: str = ""  # 数据来源标识


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

                tickets.append(
                    UniverseTicket(
                        ticker=ticker,
                        name=name,
                        price=self._safe_float(row.get("最新价")),
                        pe=self._safe_float(row.get("市盈率-动态")),
                        pb=self._safe_float(row.get("市净率")),
                        market_cap=self._safe_float(row.get("总市值")),
                        volume_ratio=self._safe_float(row.get("量比")),
                        turnover_rate=self._safe_float(row.get("换手率")),
                        industry=str(row.get("行业", "")) or None,
                        source=self.name,
                    )
                )

            logger.info(f"📡 {self.name} 全市场获取: {len(tickets)} 只 A 股")
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


# ── MootDx Provider ───────────────────────────────────────────────────────────


class MootDxUniverseProvider(UniverseProvider):
    """
    基于 baostock 获取股票列表 + mootdx 获取实时行情的 Provider。

    依赖：baostock（股票列表）、mootdx（实时报价）。
    无需 API Key，完全免费。
    """

    name = "mootdx"

    def get_universe(self) -> list[UniverseTicket]:
        # 1. 从 baostock 获取 A 股代码列表
        try:
            import baostock as bs  # type: ignore

            lg = bs.login()
            if lg.error_code != "0":
                logger.error(f"baostock login failed: {lg.error_msg}")
                bs.logout()
                return []

            rs = bs.query_stock_basic()
            raw_codes: list[str] = []
            while rs.next():
                row = rs.get_row_data()
                # Fields: code, code_name, ipoDate, outDate, type, status
                if len(row) < 6:
                    continue
                code = row[0]  # sh.600519 / sz.000858
                stock_type = row[4]  # '1' = A-share
                status = row[5]  # '1' = listed
                if stock_type != "1" or status != "1":
                    continue
                raw_codes.append(code)

            bs.logout()
            logger.info(f"📋 Baostock 获取到 {len(raw_codes)} 只 A 股代码")
        except Exception as e:
            logger.error(f"baostock 获取失败: {e}")
            return []

        # 2. 用 mootdx 批量获取实时行情
        try:
            from mootdx.quotes import Quotes  # type: ignore

            q = Quotes.factory(market="std")
        except Exception as e:
            logger.error(f"mootdx 初始化失败: {e}")
            return []

        tickets: list[UniverseTicket] = []
        # mootdx quotes API 每请求最多返回 80 行，批量过大会被静默截断。
        # 使用 20 作为安全批次大小，并加入间隔以避免被服务端限流。
        batch_size = 20
        import time as _time

        for batch_start in range(0, len(raw_codes), batch_size):
            batch = raw_codes[batch_start : batch_start + batch_size]
            # mootdx 需要纯数字代码（无 sh./sz. 前缀）
            plain_codes = [c.split(".")[-1] for c in batch]
            try:
                df = q.quotes(symbol=plain_codes)
                if df is not None and not df.empty:
                    for _, row in df.iterrows():
                        code = str(row.get("code", ""))
                        market = int(row.get("market", 0))
                        price = float(row.get("price", 0))
                        if price > 0:
                            ticker = f"{'sh' if market == 1 else 'sz'}.{code}"
                            tickets.append(
                                UniverseTicket(
                                    ticker=ticker,
                                    price=price,
                                    source=self.name,
                                )
                            )
                fetched = len(df) if df is not None and not df.empty else 0
                logger.debug(
                    f"  mootdx batch [{batch_start}:{batch_start + len(batch)}] → {fetched} 行"
                )
            except Exception as e:
                logger.warning(f"mootdx 批量获取失败 (batch {batch_start}): {e}")
            # 批次间隔，避免被服务端限流
            _time.sleep(0.3)

        # ── 补充行业数据（通过 baostock query_stock_industry）─────────────────
        self._fill_industry(tickets)

        logger.info(f"📡 MootDx 全市场获取: {len(tickets)} 只 A 股")
        return tickets

    def _fill_industry(self, tickets: list[UniverseTicket]) -> None:
        """
        通过 baostock query_stock_industry 补充 industry 字段。

        逐个 ticker 查询，失败时静默跳过，不影响其他股票。
        """
        try:
            import baostock as bs  # type: ignore

            lg = bs.login()
            if lg.error_code != "0":
                logger.debug(f"baostock login failed in _fill_industry: {lg.error_msg}")
                return
            try:
                for ticket in tickets:
                    try:
                        rs = bs.query_stock_industry(code=ticket.ticker)  # type: ignore
                        if rs.error_code != "0":
                            continue
                        rows: list[list] = []
                        while rs.next():
                            rows.append(rs.get_row_data())
                        if rows:
                            # baostock 返回: [industry_code, industry_name, ...]
                            row = rows[0]
                            if len(row) > 1 and row[1]:
                                ticket.industry = str(row[1]) or None
                    except Exception:
                        continue
            finally:
                bs.logout()  # type: ignore
        except ImportError:
            logger.debug("baostock 未安装，跳过行业数据补充")
        except Exception as e:
            logger.debug(f"_fill_industry 异常: {e}")


# ── 工厂函数 ──────────────────────────────────────────────────────────────────

_PROVIDER_REGISTRY: dict[str, type[UniverseProvider]] = {
    "akshare": AkshareUniverseProvider,
    "mootdx": MootDxUniverseProvider,
    # 其他 provider 可通过 _PROVIDER_REGISTRY["name"] = ClassName 注册
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
