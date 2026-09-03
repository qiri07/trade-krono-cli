"""Static Filter Stage — ST / 停牌 / 次新股 静态过滤。

利用 abnormal_stock.py 的预检能力，对全市场股票做一次性静态排除。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from trade_krono_cli.abnormal_stock import precheck_stock_status
from trade_krono_cli.universe.stages import FilterStage

if TYPE_CHECKING:
    from trade_krono_cli.universe.provider import UniverseTicket


class StaticFilterStage(FilterStage):
    """静态过滤阶段：排除 ST、停牌、退市、次新股票。

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
        exclude_low_price: bool = True,
        low_price_threshold: float = 3.0,
    ) -> None:
        self.exclude_st = exclude_st
        self.skip_suspended = skip_suspended
        self.skip_new_stock = skip_new_stock
        self.new_stock_min_days = new_stock_min_days
        self.batch_size = batch_size
        self.exclude_low_price = exclude_low_price
        self.low_price_threshold = low_price_threshold

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
            batch = tickets[i : i + self.batch_size]
            batch_tickers = [t.ticker for t in batch]

            try:
                flags_map = precheck_stock_status(
                    batch_tickers,
                    eval_date="",  # 使用当前日期
                    min_listing_days=self.new_stock_min_days if self.skip_new_stock else 0,
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
                # 低价股过滤
                if self.exclude_low_price and ticket.price is not None:
                    if ticket.price < self.low_price_threshold:
                        rejected_count += 1
                        continue
                kept.append(ticket)

        logger.info(
            f"📋 Static stage: {len(tickets)} → {len(kept)} "
            f"(排除 {rejected_count} 只: ST/停牌/次新/低价)",
        )

        # 低价股过滤（独立于 precheck，确保始终生效）
        if self.exclude_low_price:
            pre_lp = len(kept)
            kept = [t for t in kept if t.price is None or t.price >= self.low_price_threshold]
            rejected_count += pre_lp - len(kept)
            logger.info(
                f"📋 Static stage low-price: {pre_lp} → {len(kept)} "
                f"(排除 {pre_lp - len(kept)} 只 < {self.low_price_threshold}元)",
            )

        return kept
