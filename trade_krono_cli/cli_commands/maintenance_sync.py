"""同步命令 — 向后兼容薄包装。

实际实现已拆分到以下子模块：
  _sync_helpers.py   — 共享工具函数（信号量、健康检查、并发拉取）
  sync_universe.py   — sync-universe 命令
  sync_whitelist.py  — sync-whitelist 命令
  rank_providers.py  — rank-providers 命令
"""

from __future__ import annotations

# 向后兼容导出（原有 import 路径无需修改）
from trade_krono_cli.cli_commands._sync_helpers import (
    _check_provider_health,
    _fetch_ticker_parallel,
    _resolve_tickers,
)
from trade_krono_cli.cli_commands.core import _load_env
from trade_krono_cli.cli_commands.rank_providers import rank_providers
from trade_krono_cli.cli_commands.sync_universe import sync_universe
from trade_krono_cli.cli_commands.sync_whitelist import sync_whitelist

__all__ = [
    "_check_provider_health",
    "_fetch_ticker_parallel",
    "_load_env",
    "_resolve_tickers",
    "rank_providers",
    "sync_universe",
    "sync_whitelist",
]
