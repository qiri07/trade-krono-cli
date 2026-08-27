"""
CLI 命令实现 — 各命令的业务逻辑（向后兼容薄包装）。

本文件已被 trade_krono_cli/cli_commands/ 包替代。
为保持旧 import 路径兼容，此处仅重新导出所有公开 API。
"""
from __future__ import annotations

from trade_krono_cli.cli_commands.core import (  # noqa: F401
    _build_degrade_overrides,
    _load_env,
    _load_tickers,
    _sanitize_path,
    run,
    ta,
    kronos,
)
from trade_krono_cli.cli_commands.repo import (  # noqa: F401
    repo_status,
    repo_doctor,
    repo_update,
    repo_pin,
)
from trade_krono_cli.cli_commands.maintenance import (  # noqa: F401
    status,
    clear_cache,
    warm_cache,
    history,
    eval_prediction,
    retry_failed,
)

__all__ = [
    "_build_degrade_overrides",
    "_load_env",
    "_load_tickers",
    "_sanitize_path",
    "run",
    "ta",
    "kronos",
    "repo_status",
    "repo_doctor",
    "repo_update",
    "repo_pin",
    "status",
    "clear_cache",
    "warm_cache",
    "history",
    "eval_prediction",
    "retry_failed",
]
