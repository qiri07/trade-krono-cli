"""scripts._utils.schedule — 交易日与数据新鲜度判断。

提供期望交易日计算，用于判断 K 线缓存是否已更新至预期日期。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from scripts._utils.constants import MARKET_CLOSE_HOUR, MARKET_CLOSE_MINUTE


def expected_trading_day(now: datetime | None = None) -> str:
    """返回此刻应已落库的最近交易日（YYYY-MM-DD）。

    收盘后（>= 15:00）返回当日；盘中返回上一交易日；周末回退到周五。

    Args:
        now: 用于测试注入的时间，缺省取 ``datetime.now()``。

    Returns:
        日期字符串，如 ``2026-11-10``。
    """
    current = now or datetime.now()
    day = current.date()
    after_close = (current.hour, current.minute) >= (
        MARKET_CLOSE_HOUR,
        MARKET_CLOSE_MINUTE,
    )
    if day.weekday() >= 5:  # 周六/周日 → 回退到周五
        day -= timedelta(days=day.weekday() - 4)
    elif not after_close:
        day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day.strftime("%Y-%m-%d")
