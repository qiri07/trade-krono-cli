"""scripts/hk_screen/__init__.py — 港股筛选模块

提供基于详细规则表的港股筛选功能：

1. 交易规则硬过滤（港股通资格、计价货币、上市时长、标的类型、停牌、监管警示）
2. 流动性与市值过滤（成交额、换手率、市值、成交/市值比）
3. 财务基本面排雷（扣非盈利、审计意见、资产负债率、经营现金流、营收增速、毛利率、ROE）
4. 庄股与异常标的过滤（股东集中度、回撤、供股、股价、从高点跌幅、单日涨幅）
5. 优质标的精选加分（分红、派息比、股息率、波动率、FCF/净利、PE）

用法：
    uv run python scripts/hk_screen/cli.py
"""

from scripts.hk_screen.data_fetcher import (
    fetch_hk_raw_data,
    get_hk_connect_stocks,
    get_hk_stock_list_from_akshare,
    get_hk_stock_list_from_cache,
    raw_to_screen_result,
)
from scripts.hk_screen.screener import (
    HKScreenResult,
    compute_quality_score,
    count_passed_rules,
    print_screen_summary,
    screen_hk_stocks,
)

__all__ = [
    "HKScreenResult",
    "compute_quality_score",
    "count_passed_rules",
    "print_screen_summary",
    "screen_hk_stocks",
    "fetch_hk_raw_data",
    "get_hk_connect_stocks",
    "get_hk_stock_list_from_cache",
    "get_hk_stock_list_from_akshare",
    "raw_to_screen_result",
]
