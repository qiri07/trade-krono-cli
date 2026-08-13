"""
Static Filter Stage — ST / 停牌 / 次新股 静态过滤。

利用 abnormal_stock.py 的预检能力，对全市场股票做一次性静态排除。
"""
from __future__ import annotations

from loguru import logger

from trade_krono_cli.universe.stages import FilterStage
from trade_krono_cli.universe.provider import UniverseTicket
from trade_krono_cli.abnormal_stock import precheck_stock_status
from trade_krono_cli.security import validate_ticker


class StaticFilterStage(FilterStage):
    """
    静态过滤阶段：排除 ST、停牌、退市、次新股票。

    这是第一层过滤，在基本面数据获取之前进行，
    因为异常标记检查不需要额外的财务数据。
    """

    name = "static"

    def __init__(
        self,
        exclude_st: bool = True,
        skip_suspended: bool = True,
        skip_new_stock: bool = True,
        new_stock_min_days: int = 60,
        batch_size: int = 200,
    ):
        self.exclude_st = exclude_st
        self.skip_suspended = skip_suspended
        self.skip_new_stock = skip_new_stock
        self.new_stock_min_days = new_stock_min_days
        self.batch_size = batch_size

    def filter(self, tickets: list[UniverseTicket]) -> list[UniverseTicket]:
        if not tickets:
            return []

        blocked_flags = set()
        if self.exclude_st:
            blocked_flags.add("ST")
        if self.skip_suspended:
            blocked_flags.add("SUSPENDED")

        kept: list[UniverseTicket] = []
        rejected_count = 0

        # 分批预检（避免单次调用过多 ticker）
        for i in range(0, len(tickets), self.batch_size):
            batch = tickets[i:i + self.batch_size]
            batch_tickers = [t.ticker for t in batch]

            try:
                flags_map = precheck_stock_status(
                    batch_tickers,
                    eval_date="",  # 使用当前日期
                    min_listing_days=self.new_stock_min_days
                    if self.skip_new_stock
                    else 0,
                    skip_suspended=self.skip_suspended,
                )
            except Exception as e:
                logger.warning(f"Static stage precheck 异常: {e}，跳过本批")
                kept.extend(batch)
                continue

            for ticket in batch:
                af = flags_map.get(ticket.ticker)
                if af is None:
                    kept.append(ticket)
                    continue

                flag_set = set(af.flag_names())
                if self.skip_new_stock and "NEW_STOCK" in flag_set:
                    rejected_count += 1
                    continue
                if blocked_flags & flag_set:
                    rejected_count += 1
                    continue
                kept.append(ticket)

        logger.info(
            f"📋 Static stage: {len(tickets)} → {len(kept)} "
            f"(排除 {rejected_count} 只: ST/停牌/次新)"
        )
        return kept
