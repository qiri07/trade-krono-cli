"""T+1 交易约束追踪器。

从 trading_constraints.py 拆分，职责单一：
  · 跟踪当日买入记录，支持 T+1 结算约束检查

T+1 规则：A 股买入当日不可卖出，次日及之后可卖出。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta


class T1Tracker:
    """跟踪当日买入记录，支持 T+1 结算约束检查。

    线程安全：内部使用 dict，建议每次 pipeline run 创建新实例。
    """

    def __init__(self) -> None:
        # ticker -> buy_date (str "YYYY-MM-DD")
        self._buys: dict[str, str] = {}

    def record_buy(self, ticker: str, buy_date: str) -> None:
        """记录一笔买入。"""
        self._buys[ticker] = buy_date

    def can_sell(self, ticker: str, sell_date: str) -> bool:
        """检查是否可以在 sell_date 卖出 ticker。

        T+1 规则：买入当日不能卖出，次日及之后可以。
        """
        buy_date = self._buys.get(ticker)
        if buy_date is None:
            return True  # 无买入记录，可以自由卖出
        # 简单日期比较：sell_date > buy_date
        return sell_date > buy_date

    def locked_until(self, ticker: str) -> date | None:
        """返回 ticker 被锁定的最早解锁日期。"""
        buy_date = self._buys.get(ticker)
        if buy_date is None:
            return None
        try:
            bd = datetime.strptime(buy_date, "%Y-%m-%d").date()
            return bd + timedelta(days=1)
        except ValueError:
            return None

    def clear(self) -> None:
        """清空所有买入记录（新交易日开始时调用）。"""
        self._buys.clear()
