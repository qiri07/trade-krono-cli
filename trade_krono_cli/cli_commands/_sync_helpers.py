"""同步命令共享工具 — 向后兼容薄包装。

实际实现已拆分到以下子模块：
  sync_helpers/semaphore.py  — 信号量管理
  sync_helpers/ticker.py     — 股票代码解析
  sync_helpers/health.py     — Provider 健康检查
  sync_helpers/fetcher.py    — 并发 K 线拉取
  sync_helpers/__init__.py   — _run_sync 主逻辑

原有 import 路径（trade_krono_cli.cli_commands._sync_helpers）继续有效。
"""

from __future__ import annotations

# 向后兼容导出（原有 import 路径无需修改）
from trade_krono_cli.cli_commands.sync_helpers import (  # noqa: F401
    _BETWEEN_REQUEST_DELAY,
    _HEALTH_CHECK_INTERVAL_TICKERS,
    _HEALTH_CHECK_MIN_INTERVAL_SEC,
    _HEALTH_REMOVE_THRESHOLD,
    _HEALTH_RESTORE_THRESHOLD,
    _check_provider_health,
    _fetch_ticker_parallel,
    _get_global_semaphore,
    _get_provider_semaphore,
    _resolve_tickers,
    _run_sync,
    _update_usable_providers,
    console,
)

__all__ = [
    "_check_provider_health",
    "_fetch_ticker_parallel",
    "_get_global_semaphore",
    "_get_provider_semaphore",
    "_HEALTH_CHECK_INTERVAL_TICKERS",
    "_HEALTH_CHECK_MIN_INTERVAL_SEC",
    "_HEALTH_REMOVE_THRESHOLD",
    "_HEALTH_RESTORE_THRESHOLD",
    "_resolve_tickers",
    "_run_sync",
    "_update_usable_providers",
    "console",
]
