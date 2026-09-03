"""CLI 命令包入口。

向后兼容：保留 trade_krono_cli.cli_commands 的导入路径。
所有公开 API 通过此模块导出，原有 import 语句无需修改。
"""

from __future__ import annotations

# 共享工具函数（cli.py 和测试直接导入）
from trade_krono_cli.cli_commands.core import (
    _build_degrade_overrides,
    _load_env,
    _load_tickers,
    _sanitize_path,
    kronos,
    run,
    ta,
)

# 数据导出命令
from trade_krono_cli.cli_commands.export_daily_pv import export_daily_pv

# 维护命令
from trade_krono_cli.cli_commands.maintenance import (
    clear_cache,
    eval_prediction,
    history,
    rank_providers,
    retry_failed,
    status,
    sync_universe,
    sync_whitelist,
    warm_cache,
)

# Repo 子命令
from trade_krono_cli.cli_commands.repo import (
    repo_doctor,
    repo_pin,
    repo_status,
    repo_update,
)

__all__ = [
    "_build_degrade_overrides",
    "_load_env",
    "_load_tickers",
    "_sanitize_path",
    "clear_cache",
    "eval_prediction",
    "export_daily_pv",
    "history",
    "kronos",
    "rank_providers",
    "repo_doctor",
    "repo_pin",
    "repo_status",
    "repo_update",
    "retry_failed",
    "run",
    "status",
    "sync_universe",
    "sync_whitelist",
    "ta",
    "warm_cache",
]
