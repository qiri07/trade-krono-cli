"""fill_missing 选股 — 日期相关。

提供动态获取今日日期与「应已落库」的最近交易日。
"""

from __future__ import annotations

import time

from scripts._utils import expected_trading_day
from scripts.fill_missing.constants import START_DATE  # noqa: F401  (供兼容导入)

# 首次运行时缓存今日日期，避免跨批次日期变化
_today: str | None = None


def get_today_date() -> str:
    """动态获取今日日期（YYYY-MM-DD 格式），并缓存供后续调用复用。"""
    global _today
    if _today is None:
        _today = time.strftime("%Y-%m-%d")
    return _today


def get_expected_date() -> str:
    """获取此刻「应已落库」的最近交易日（收盘后为当日，盘中为上一交易日）。

    与 :func:`get_today_date` 的区别：开盘前/盘中调用时，当日 K 线尚不存在，
    若按当日判断会把全库标的误判为「缺今日数据」。

    Returns:
        日期字符串，如 ``2026-11-10``。
    """
    return expected_trading_day()
