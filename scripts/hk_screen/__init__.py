"""scripts/hk_screen/__init__.py — 港股筛选模块

提供港股筛选的完整能力：

1. 数据获取（本地 K 线 + ``hk_metadata`` 元数据，实时快照兜底）
2. 交易规则硬过滤（港股通、计价货币、上市时长、标的类型、停牌、监管警示）
3. 流动性与市值过滤（成交额、换手率、市值）
4. 财务基本面排雷（盈利、审计、资产负债率、经营现金流、营收增速、毛利率、ROE）
5. 庄股与异常标的过滤（股东集中度、回撤、供股、股价、从高点跌幅、单日涨幅）
6. 优质标的精选加分（分红、派息比、股息率、波动率、FCF/净利、PE）

统一入口：
    uv run python scripts/hk_daily_screen.py
"""

from scripts.hk_screen.data_fetcher import (
    HKRawData,
    fetch_hk_raw_data,
    get_hk_connect_stocks,
    get_hk_stock_list_from_akshare,
    get_hk_stock_list_from_cache,
    load_hk_metadata,
    raw_to_screen_result,
)
from scripts.hk_screen.metadata_store import (
    ensure_hk_metadata_table,
    has_financial_data,
    has_profile_data,
    read_hk_metadata,
    upsert_hk_metadata,
)
from scripts.hk_screen.screener import (
    HKScreenResult,
    RuleVerdict,
    compute_quality_score,
    count_passed_rules,
    normalize_dividend_yield,
    print_screen_summary,
    screen_hk_stocks,
)

__all__ = [
    "HKScreenResult",
    "HKRawData",
    "RuleVerdict",
    "compute_quality_score",
    "count_passed_rules",
    "normalize_dividend_yield",
    "print_screen_summary",
    "screen_hk_stocks",
    "fetch_hk_raw_data",
    "get_hk_connect_stocks",
    "get_hk_stock_list_from_cache",
    "get_hk_stock_list_from_akshare",
    "load_hk_metadata",
    "raw_to_screen_result",
    "ensure_hk_metadata_table",
    "read_hk_metadata",
    "upsert_hk_metadata",
    "has_financial_data",
    "has_profile_data",
]
