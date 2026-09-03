"""Factor Filter Stage — 量比 / 换手率 流动性过滤。

在静态和基本面过滤之后，通过成交活跃度排除流动性不足的标的。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from trade_krono_cli.universe.stages import FilterStage

if TYPE_CHECKING:
    from trade_krono_cli.universe.provider import UniverseTicket


class FactorFilterStage(FilterStage):
    """因子过滤阶段：流动性相关指标。

    过滤条件（全部可选，None 表示不限制）：
      - min_volume_ratio: 最小量比
      - min_turnover_rate: 最小换手率（%）
      - min_volume: 最小成交量（手），低于此值排除
    """

    name = "factor"

    def __init__(
        self,
        min_volume_ratio: float | None = None,
        min_turnover_rate: float | None = None,
        min_volume: float | None = None,
    ) -> None:
        self.min_volume_ratio = min_volume_ratio
        self.min_turnover_rate = min_turnover_rate
        self.min_volume = min_volume

    def filter(self, tickets: list[UniverseTicket]) -> list[UniverseTicket]:
        if not tickets:
            return []

        kept: list[UniverseTicket] = []

        for t in tickets:
            if self.min_volume_ratio is not None and t.volume_ratio is not None:
                if t.volume_ratio < self.min_volume_ratio:
                    continue

            if self.min_turnover_rate is not None and t.turnover_rate is not None:
                if t.turnover_rate < self.min_turnover_rate:
                    continue

            if self.min_volume is not None and t.volume is not None:
                if t.volume < self.min_volume:
                    continue

            kept.append(t)

        logger.info(f"📋 Factor stage: {len(tickets)} → {len(kept)}")
        return kept
