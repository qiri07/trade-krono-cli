"""补齐失败股票历史数据 + 今日增量同步。

本模块为向后兼容层，所有实现已拆分至 :mod:`scripts.fill_missing` 包。
本模块重新导出 ``scripts.fill_missing`` 的所有公共符号，确保现有导入
（如 ``from scripts.fill_missing_and_today import ...``）继续正常工作。

用法::

    uv run python scripts/fill_missing_and_today.py
    uv run python scripts/fill_missing_and_today.py --plan plan.json
    uv run python scripts/fill_missing_and_today.py --dry-run
    uv run python scripts/fill_missing_and_today.py --no-incremental
    uv run python scripts/fill_missing_and_today.py --no-backup
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.fill_missing import (  # noqa: F401, E402
    CACHE_DB,
    FAILED_MIN_RECORDS,
    FULL_HISTORY_START,
    PROGRESS_FILE,
    PROGRESS_FLUSH_EVERY,
    START_DATE,
    WORKERS,
    _flush_completed,
    _has_sufficient_history,
    _has_today_data,
    _load_completed_tickers,
    _load_failed_tickers,
    _mark_completed,
    _mark_failed,
    _save_progress,
    fetch_missing_report,
    get_expected_date,
    get_failed_tickers,
    get_insufficient_history_tickers,
    get_today_date,
    get_today_only_tickers,
    load_sync_plan,
    main,
    print_missing_report,
)
from scripts.fill_missing import (  # noqa: E402
    FETCH_TIMEOUT as _DEFAULT_FETCH_TIMEOUT,
)
from scripts.fill_missing import (  # noqa: E402
    _fetch_kline_provider as _inner_fetch_kline_provider,
)
from scripts.fill_missing import (  # noqa: E402
    fetch_full_range as _inner_fetch_full_range,
)
from scripts.fill_missing import (  # noqa: E402
    fetch_today_incremental as _inner_fetch_today_incremental,
)
from trade_krono_cli.data_providers.factory import get_data_factory  # noqa: F401, E402

# FETCH_TIMEOUT 作为模块级变量，以便测试通过 monkeypatch.setattr 修改
FETCH_TIMEOUT = _DEFAULT_FETCH_TIMEOUT


def _fetch_kline_provider(
    factory: Any,
    ticker: str,
    provider_name: str,
    start_date: str,
    end_date: str,
) -> tuple[Any | None, Exception | None, bool]:
    """兼容层包装：从 fmat.FETCH_TIMEOUT 获取超时值，以便测试可 monkeypatch。"""
    return _inner_fetch_kline_provider(
        factory, ticker, provider_name, start_date, end_date, fetch_timeout=FETCH_TIMEOUT
    )


def fetch_full_range(
    factory: Any,
    ticker: str,
    provider_chain: list[str],
) -> tuple[str, int, str]:
    """兼容层包装：从 fmat.FETCH_TIMEOUT 获取超时值。"""
    return _inner_fetch_full_range(factory, ticker, provider_chain, fetch_timeout=FETCH_TIMEOUT)


def fetch_today_incremental(
    factory: Any,
    ticker: str,
    provider_chain: list[str],
) -> tuple[str, int, str]:
    """兼容层包装：从 fmat.FETCH_TIMEOUT 获取超时值。"""
    return _inner_fetch_today_incremental(
        factory, ticker, provider_chain, fetch_timeout=FETCH_TIMEOUT
    )


__all__ = [
    # constants
    "START_DATE",
    "WORKERS",
    "FETCH_TIMEOUT",
    "CACHE_DB",
    "PROGRESS_FILE",
    "PROGRESS_FLUSH_EVERY",
    "FAILED_MIN_RECORDS",
    "FULL_HISTORY_START",
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
    "get_insufficient_history_tickers",
    "get_today_only_tickers",
    "get_data_factory",
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

if __name__ == "__main__":
    # 必须走 run_cli：直接调用 main() 会丢弃 --plan / --no-backup 等命令行参数
    from scripts.fill_missing.cli import run_cli

    run_cli()
