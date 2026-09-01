"""
Universe Provider — 全市场股票列表获取接口。

定义 UniverseProvider ABC 以及基于 akshare / mootdx 的实现。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional

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
    volume: Optional[float] = None  # 成交量（手，akshare 单位）
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

    @staticmethod
    def _safe_float(value) -> Optional[float]:
        """安全地将值转换为 float，处理 None/无效值。"""
        if value is None:
            return None
        try:
            f = float(value)
            if f != f or f == float("inf") or f == float("-inf"):
                return None
            return f
        except (ValueError, TypeError):
            return None

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
                        volume=self._safe_float(row.get("成交量")),
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

    def __init__(self, populate_market_cap: bool = False):
        """
        Parameters
        ----------
        populate_market_cap : bool
            是否通过 mootdx finance API 补充总市值数据（会增加 ~3-4 分钟延迟）。
        """
        self._populate_market_cap = populate_market_cap

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

            # ── 在同一 session 内批量补充行业数据，避免第二次 login/logout ──────
            industry_map: dict[str, str] = {}
            try:
                ind_rs = bs.query_stock_industry()
                while ind_rs.next():
                    ind_row = ind_rs.get_row_data()
                    if len(ind_row) > 1 and ind_row[1]:
                        industry_map[ind_row[0]] = str(ind_row[1])
            except Exception as e:
                logger.debug(f"baostock 行业查询失败（非致命）: {e}")

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
            logger.warning(f"mootdx 初始化失败，降级为仅 baostock 数据（无实时行情）: {e}")
            # 降级：返回 baostock 原始代码列表，不阻塞流水线
            fallback_tickets: list[UniverseTicket] = [
                UniverseTicket(ticker=c, source=self.name) for c in raw_codes
            ]
            logger.info(f"📡 MootDx 降级获取: {len(fallback_tickets)} 只 A 股（无行情）")
            return fallback_tickets

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
                            # vol 单位已经是手（lot），无需转换
                            tickets.append(
                                UniverseTicket(
                                    ticker=ticker,
                                    price=price,
                                    volume=self._safe_float(row.get("vol")),
                                    industry=industry_map.get(ticker),
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

        logger.info(f"📡 MootDx 全市场获取: {len(tickets)} 只 A 股")

        # ── 补充 market_cap：通过 mootdx finance 获取总股本，计算市值 ──────
        if self._populate_market_cap:
            logger.info("📊 正在补充市值数据（mootdx finance）...")
            code_to_ticket = {t.ticker: t for t in tickets}
            updated = 0
            failed = 0
            for i, ticker in enumerate([t.ticker for t in tickets]):
                plain_code = ticker.split(".")[-1]
                try:
                    fin_df = q.finance(symbol=plain_code)
                    if fin_df is not None and not fin_df.empty:
                        zongguben = fin_df["zongguben"].values[0]  # 总股本（股）
                        t = code_to_ticket.get(ticker)
                        if t and t.price:
                            price = t.price
                            # 市值（亿元）= 总股本 × 价格 / 1亿
                            t.market_cap = round(zongguben * price / 1e8, 2)
                            updated += 1
                except Exception:
                    failed += 1
                # 每 50 只打印进度
                if (i + 1) % 50 == 0:
                    logger.debug(
                        f"  市值数据进度: {i + 1}/{len(tickets)} (已更新={updated}, 失败={failed})"
                    )
            logger.info(f"📊 市值数据补充完成: {updated}/{len(tickets)} 只成功, {failed} 只失败")

        return tickets


# ── 同花顺 Universe Provider ───────────────────────────────────────────────────


class TongHuaShunUniverseProvider(UniverseProvider):
    """
    基于同花顺（fuyao）REST API 的 A 股宇宙提供者。

    依赖 HITHINK_FINANCE_API_KEY 环境变量（兼容 FUYAO_API_KEY）。
    通过 /api/meta/tickers/list 获取全市场 A 股列表，
    通过 /api/a-share/prices/snapshot 批量拉取实时行情（含价格、成交量）。
    不依赖 mootdx finance API，因此 populate_market_cap 参数无效。
    """

    name = "tonghuashun"

    def __init__(self, populate_market_cap: bool = False):
        self._populate_market_cap = populate_market_cap

    def get_universe(self) -> list[UniverseTicket]:
        import os
        import time as _time

        api_key = (
            os.getenv("HITHINK_FINANCE_API_KEY", "").strip()
            or os.getenv("FUYAO_API_KEY", "").strip()
        )
        if not api_key:
            logger.warning(
                "HITHINK_FINANCE_API_KEY / FUYAO_API_KEY 未配置，无法使用同花顺 UniverseProvider"
            )
            return []

        try:
            import requests
        except ImportError:
            logger.warning("requests 未安装，无法使用同花顺 UniverseProvider")
            return []

        # 1. 分页拉取全市场 A 股代码表
        all_items: list[dict[str, Any]] = []
        offset = 0
        page_size = 1000
        while True:
            try:
                resp = requests.get(
                    "https://fuyao.aicubes.cn/api/meta/tickers/list",
                    params=[("asset_type", "a-share"), ("limit", page_size), ("offset", offset)],
                    headers={"X-api-key": api_key},
                    timeout=30,
                )
                resp.raise_for_status()
                body = resp.json()
                if body.get("code") != 0:
                    logger.warning(
                        f"同花顺 ticker list code={body.get('code')} msg={body.get('message')}"
                    )
                    break
                items = body.get("data", {}).get("item", [])
                if not items:
                    break
                all_items.extend(items)
                if len(items) < page_size:
                    break
                offset += page_size
            except Exception as e:
                logger.warning(f"同花顺 ticker list 分页拉取失败 (offset={offset}): {e}")
                break

        logger.info(f"📋 同花顺获取到 {len(all_items)} 只 A 股代码")

        # 2. 批量拉取行情快照（每批 200 只）
        batch_size = 200
        tickets: list[UniverseTicket] = []

        for i in range(0, len(all_items), batch_size):
            batch = all_items[i : i + batch_size]
            thscodes = ",".join(item["thscode"] for item in batch)
            try:
                resp = requests.get(
                    "https://fuyao.aicubes.cn/api/a-share/prices/snapshot",
                    params={"thscodes": thscodes},
                    headers={"X-api-key": api_key},
                    timeout=30,
                )
                resp.raise_for_status()
                body = resp.json()
                if body.get("code") == 0:
                    for item in body.get("data", {}).get("item", []):
                        ticker = self._thscode_to_ticker(item.get("thscode", ""))
                        if ticker:
                            tickets.append(
                                UniverseTicket(
                                    ticker=ticker,
                                    name=item.get("ticker", ""),
                                    price=self._safe_float(item.get("last_price")),
                                    volume=self._safe_float(item.get("volume")),
                                    source=self.name,
                                )
                            )
            except Exception as e:
                logger.warning(f"同花顺行情批量拉取失败 (batch {i}): {e}")
            _time.sleep(0.2)  # 限流保护

        logger.info(f"📡 同花顺全市场获取: {len(tickets)} 只 A 股")
        return tickets

    @staticmethod
    def _thscode_to_ticker(thscode: str) -> str:
        """600519.SH → sh.600519 / 000858.SZ → sz.000858"""
        if "." not in thscode:
            return ""
        code, exchange = thscode.rsplit(".", 1)
        prefix = exchange.lower()
        if prefix in ("sh", "sz", "bj"):
            return f"{prefix}.{code}"
        return ""


# ── 工厂函数 ──────────────────────────────────────────────────────────────────

_PROVIDER_REGISTRY: dict[str, type[UniverseProvider]] = {
    "akshare": AkshareUniverseProvider,
    "mootdx": MootDxUniverseProvider,
    "tonghuashun": TongHuaShunUniverseProvider,
    # 其他 provider 可通过 _PROVIDER_REGISTRY["name"] = ClassName 注册
}


def get_universe_provider(
    source: str,
    populate_market_cap: bool = False,
) -> Optional[UniverseProvider]:
    """
    根据 source 名称获取对应的 UniverseProvider 实例。

    Parameters
    ----------
    source : str
        数据源名称，如 "akshare"
    populate_market_cap : bool
        是否补充市值数据（仅对 mootdx 有效）。

    Returns
    -------
    UniverseProvider 或 None（不支持时）
    """
    cls = _PROVIDER_REGISTRY.get(source)
    if cls is None:
        logger.warning(f"不支持的 universe_source: {source}，使用默认 akshare")
        cls = _PROVIDER_REGISTRY.get("akshare")
    if cls is None:
        return None
    # mootdx / tonghuashun 需要传入 populate_market_cap 参数（tonghuashun 当前不使用）
    if cls in (MootDxUniverseProvider, TongHuaShunUniverseProvider):
        return cls(populate_market_cap=populate_market_cap)
    return cls()
