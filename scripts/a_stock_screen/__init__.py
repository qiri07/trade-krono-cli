"""scripts/a_stock_screen — A股筛选模块。

参考 scripts/hk_screen 的港股筛选规则，为 A股 提供对应的流动性、
市值、价格、停牌、ST 等硬过滤规则，供今日流程汇集结果时使用。

筛选阶段：
  1. 交易规则硬过滤（剔除 ST、北交所、ETF、停牌）
  2. 流动性与市值过滤（近20日日均成交额、近12月日均换手率、总市值）
  3. 异常标的过滤（最新股价）

五必筛选（适用于流程之外的独立筛选）：
  - scripts.a_stock_screen.fivemust.screen_five_must
  - scripts.a_stock_screen.fivemust.FiveMustResult

用法：
    from scripts.a_stock_screen.screener import screen_a_stock_candidates
    passed, details = screen_a_stock_candidates(codes, date_str)
"""

from scripts.a_stock_screen.fivemust import FiveMustResult, screen_five_must
from scripts.a_stock_screen.screener import (
    AStockScreenResult,
    count_passed_rules,
    screen_a_stock_candidates,
)

__all__ = [
    "AStockScreenResult",
    "FiveMustResult",
    "count_passed_rules",
    "screen_a_stock_candidates",
    "screen_five_must",
]
