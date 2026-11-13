"""double_ma 包 — 双均线金叉筛选器（统一版）：日+周 MA5>MA13，排除ST，PE(0,30)，MACD日线>0。

本包由原 ``scripts/double_ma_golden_cross.py`（770 行）拆分而成，按功能模块组织：

- :mod:`scripts.double_ma.constants`   — 配置常量 (MA_FAST, MA_SLOW, PE_MIN, PE_MAX, ROE_THRESHOLD, PROFIT_THRESHOLD, API_KEY, HEADERS, BASE)
- :mod:`scripts.double_ma.utils`       — 工具函数 (_limit_pct / load_metadata / get_st_tickers / calc_ma_ratio / calc_macd)
- :mod:`scripts.double_ma.roe`         — ROE 相关 (fetch_roe_from_api / load_roe_from_cache / load_profit_from_cache)
- :mod:`scripts.double_ma.pe`          — PE 相关 (_fetch_pe_batch_api / _backfill_pe_from_api / _fill_pe_and_industry)
- :mod:`scripts.double_ma.screener`    — 核心筛选逻辑 (run_lite / run_full / process_ticker)
- :mod:`scripts.double_ma.cards`       — 打印表格 (_print_top30)
- :mod:`scripts.double_ma.cli`         — CLI 入口 (main)

向后兼容：原 ``scripts.double_ma_golden_cross`` 模块作为兼容层，重新导出本包的所有公共符号。
"""

from __future__ import annotations

from scripts.double_ma.cards import _print_top30
from scripts.double_ma.constants import (
    API_KEY,
    BASE,
    HEADERS,
    MA_FAST,
    MA_SLOW,
    PE_MAX,
    PE_MIN,
    PROFIT_THRESHOLD,
    ROE_THRESHOLD,
)
from scripts.double_ma.pe import _backfill_pe_from_api, _fetch_pe_batch_api, _fill_pe_and_industry
from scripts.double_ma.roe import fetch_roe_from_api, load_profit_from_cache, load_roe_from_cache
from scripts.double_ma.screener import process_ticker, run_full, run_lite
from scripts.double_ma.utils import (
    _limit_pct,
    calc_ma_ratio,
    calc_macd,
    get_st_tickers,
    load_metadata,
)

__all__ = [
    # constants
    "MA_FAST",
    "MA_SLOW",
    "PE_MIN",
    "PE_MAX",
    "ROE_THRESHOLD",
    "PROFIT_THRESHOLD",
    "API_KEY",
    "HEADERS",
    "BASE",
    # utils
    "_limit_pct",
    "load_metadata",
    "get_st_tickers",
    "calc_ma_ratio",
    "calc_macd",
    # roe
    "fetch_roe_from_api",
    "load_roe_from_cache",
    "load_profit_from_cache",
    # pe
    "_fetch_pe_batch_api",
    "_backfill_pe_from_api",
    "_fill_pe_and_industry",
    # screener
    "process_ticker",
    "run_lite",
    "run_full",
    # cards
    "_print_top30",
    # cli
    "main",
]
