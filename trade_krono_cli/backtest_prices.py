"""trade_krono_cli.backtest_prices — 回测价格获取与交易日辅助。

职责（从 backtest_engine.py 提取）：
  · 入场价 / 出场价获取（市价单 / 限价单）
  · 下一交易日查找
  • 周初 / 月初计算（调仓触发点）
"""

from __future__ import annotations

from datetime import datetime, timedelta


def _next_trading_day(d: datetime, kline_dates: list[str]) -> str | None:
    """在 kline_dates 中找 d 之后最近的一个交易日。"""
    ds = d.strftime("%Y-%m-%d")
    for dd in kline_dates:
        if dd > ds:
            return dd
    return None


def _week_start(d: datetime) -> datetime:
    """返回本周周一（调仓触发点）。"""
    return d - timedelta(days=d.weekday())


def _month_start(d: datetime) -> datetime:
    """返回本月第一天。"""
    return d.replace(day=1)


def get_entry_price(
    ticker: str,
    date: str,
    prev_close_map: dict[str, float],
    order_type: str,
) -> float | None:
    """获取入场价。

    - 市价单：使用 prev_close 作为近似
    - 限价单：使用 prev_close * 0.999（略低于前收，确保成交）
    """
    price = prev_close_map.get(ticker)
    if price is None:
        return None
    if order_type == "limit":
        # 限价单：设置略低于前收的价格，确保成交
        return price * 0.999
    return price


def get_exit_price(
    ticker: str,
    date: str,
    prev_close_map: dict[str, float],
    order_type: str,
) -> float | None:
    """获取出场价。

    - 市价单：使用 prev_close 作为近似
    - 限价单：使用 prev_close * 1.001（略高于前收，确保成交）
    """
    price = prev_close_map.get(ticker)
    if price is None:
        return None
    if order_type == "limit":
        return price * 1.001
    return price
