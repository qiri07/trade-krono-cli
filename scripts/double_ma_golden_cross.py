"""双均线金叉筛选器（统一版）：日+周 MA5>MA13，排除ST，PE(0,30)，MACD日线>0。

本模块为向后兼容层，所有实现已拆分至 :mod:`scripts.double_ma` 包。
本模块重新导出 ``scripts.double_ma`` 的所有公共符号，确保现有导入
（如 ``from scripts.double_ma_golden_cross import ...``）继续正常工作。

两档模式：
  - 轻量模式（--lite）：仅技术分析 MA+PE+MACD（原 double_ma_screener.py 功能）
  - 完整模式（默认）：两阶段筛选，技术分析后追加 ROE>10% + 净利润>1亿
    （ROE 优先读取 buffett_cache.db，缺失部分从同花顺 Fuyao API 补充）

用法：
  # 完整模式（默认）：双均线金叉 + ROE + 净利润
  uv run python scripts/double_ma_golden_cross.py

  # 轻量模式：仅 MA+PE+MACD
  uv run python scripts/double_ma_golden_cross.py --lite
"""

from __future__ import annotations

from scripts.double_ma import (  # noqa: F401
    API_KEY,
    BASE,
    HEADERS,
    MA_FAST,
    MA_SLOW,
    PE_MAX,
    PE_MIN,
    PROFIT_THRESHOLD,
    ROE_THRESHOLD,
    _backfill_pe_from_api,
    _fetch_pe_batch_api,
    _fill_pe_and_industry,
    _limit_pct,
    _print_top30,
    calc_ma_ratio,
    calc_macd,
    fetch_roe_from_api,
    get_st_tickers,
    load_metadata,
    load_profit_from_cache,
    load_roe_from_cache,
    process_ticker,
    run_full,
    run_lite,
)
from scripts.double_ma.cli import main  # noqa: F401

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
