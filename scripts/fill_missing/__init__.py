"""fill_missing 包 — 补齐失败股票历史数据 + 今日增量同步。

本包由原 ``scripts/fill_missing_and_today.py``（856 行）拆分而成，按功能模块组织：

- :mod:`scripts.fill_missing.constants`   — 常量定义
- :mod:`scripts.fill_missing.progress`    — 进度文件读写（断点续传）
- :mod:`scripts.fill_missing.dates`       — 日期相关 (get_today_date / get_expected_date)
- :mod:`scripts.fill_missing.plan`        — 补拉计划加载 (load_sync_plan)
- :mod:`scripts.fill_missing.db_queries`  — 数据库查询 (_has_sufficient_history / get_failed_tickers / _has_today_data)
- :mod:`scripts.fill_missing.fetch`       — K 线拉取 (_fetch_kline_provider / fetch_full_range / fetch_today_incremental)
- :mod:`scripts.fill_missing.report`      — 缺失报告 (fetch_missing_report / print_missing_report)
- :mod:`scripts.fill_missing.cli`         — CLI 入口 (main)

向后兼容：原 ``scripts.fill_missing_and_today`` 模块作为兼容层，重新导出本包的所有公共符号。
"""

from __future__ import annotations

from scripts.fill_missing.cli import main
from scripts.fill_missing.constants import (
    CACHE_DB,
    FAILED_MIN_RECORDS,
    FETCH_TIMEOUT,
    PROGRESS_FILE,
    PROGRESS_FLUSH_EVERY,
    START_DATE,
    WORKERS,
)
from scripts.fill_missing.dates import get_expected_date, get_today_date
from scripts.fill_missing.db_queries import (
    _has_sufficient_history,
    _has_today_data,
    get_failed_tickers,
    get_today_only_tickers,
)
from scripts.fill_missing.fetch import (
    _fetch_kline_provider,
    fetch_full_range,
    fetch_today_incremental,
)
from scripts.fill_missing.plan import load_sync_plan
from scripts.fill_missing.progress import (
    _flush_completed,
    _load_completed_tickers,
    _load_failed_tickers,
    _mark_completed,
    _mark_failed,
    _save_progress,
)
from scripts.fill_missing.report import fetch_missing_report, print_missing_report

__all__ = [
    # constants
    "START_DATE",
    "WORKERS",
    "FETCH_TIMEOUT",
    "CACHE_DB",
    "PROGRESS_FILE",
    "PROGRESS_FLUSH_EVERY",
    "FAILED_MIN_RECORDS",
    # progress
    "_load_completed_tickers",
    "_load_failed_tickers",
    "_save_progress",
    "_mark_completed",
    "_mark_failed",
    "_flush_completed",
    # dates
    "get_today_date",
    "get_expected_date",
    # plan
    "load_sync_plan",
    # db_queries
    "_has_sufficient_history",
    "_has_today_data",
    "get_failed_tickers",
    "get_today_only_tickers",
    # fetch
    "_fetch_kline_provider",
    "fetch_full_range",
    "fetch_today_incremental",
    # report
    "fetch_missing_report",
    "print_missing_report",
    # cli
    "main",
]
