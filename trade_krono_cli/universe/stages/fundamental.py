"""
Fundamental Filter Stage — PE / PB / 市值 / 行业 基本面过滤。

利用 akshare 实时行情中已有的 PE/PB/市值字段，
加上 baostock 行业数据（懒加载），执行基本面筛选。
"""

from __future__ import annotations

from typing import Optional

from loguru import logger

from trade_krono_cli.universe.provider import UniverseTicket
from trade_krono_cli.universe.stages import FilterStage


class FundamentalFilterStage(FilterStage):
    """
    基本面过滤阶段。

    过滤条件（全部可选，None 表示不限制）：
      - market_cap_range: 总市值 [low, high] 亿元
      - pe_range: PE(TTM) [low, high]
      - pb_range: PB [low, high]
      - min_pb: 最低市净率（PB < min_pb 视为资不抵债风险）
      - industry_whitelist: 行业白名单（精确匹配）
      - industry_blacklist: 行业黑名单（精确匹配）
    """

    name = "fundamental"

    def __init__(
        self,
        market_cap_range: Optional[tuple[float, float]] = None,
        pe_range: Optional[tuple[float, float]] = None,
        pb_range: Optional[tuple[float, float]] = None,
        min_pb: Optional[float] = None,
        industry_whitelist: list[str] | None = None,
        industry_blacklist: list[str] | None = None,
    ):
        self.market_cap_range = market_cap_range
        self.pe_range = pe_range
        self.pb_range = pb_range
        self.min_pb = min_pb
        self.industry_whitelist = industry_whitelist or []
        self.industry_blacklist = industry_blacklist or []

    def filter(self, tickets: list[UniverseTicket]) -> list[UniverseTicket]:
        if not tickets:
            return []

        kept: list[UniverseTicket] = []

        for t in tickets:
            # ── 市值范围 ──────────────────────────────────────────
            if self.market_cap_range and t.market_cap is not None:
                low, high = self.market_cap_range
                if not (low <= t.market_cap <= high):
                    continue

            # ── PE 范围 ───────────────────────────────────────────
            if self.pe_range and t.pe is not None:
                low, high = self.pe_range
                # PE <= 0 通常为亏损股，直接排除
                if t.pe <= 0 or not (low <= t.pe <= high):
                    continue

            # ── PB 范围 ───────────────────────────────────────────
            if self.pb_range and t.pb is not None:
                low, high = self.pb_range
                if not (low <= t.pb <= high):
                    continue

            # ── 最低 PB（资不抵债风险过滤）───────────────────────
            if self.min_pb is not None and t.pb is not None:
                if t.pb < self.min_pb:
                    continue

            # ── 行业过滤（当前 tickets 暂无 industry 字段，
            #    留作后续扩展：可从 baostock 补充）────────────────
            # TODO: 接入 baostock query_stock_industry 填充 industry
            #       目前 industry_whitelist/blacklist 暂不生效

            kept.append(t)

        logger.info(f"📋 Fundamental stage: {len(tickets)} → {len(kept)}")
        return kept
