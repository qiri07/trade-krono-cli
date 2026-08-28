"""
Stage — 抽象基类：单一过滤阶段。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trade_krono_cli.universe.provider import UniverseTicket


class FilterStage(ABC):
    """
    单阶段过滤器抽象基类。

    每个 stage 接收上一阶段的 tickets，返回过滤后的子集。
    阶段内失败的 ticket 被静默丢弃并记录日志。
    """

    name: str = "base"

    @abstractmethod
    def filter(self, tickets: list[UniverseTicket]) -> list[UniverseTicket]:
        """
        对输入 ticket 列表执行过滤。

        Parameters
        ----------
        tickets : list[UniverseTicket]
            上游阶段输出的股票列表

        Returns
        -------
        list[UniverseTicket]
            通过本阶段过滤的股票
        """
        ...

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r})"
