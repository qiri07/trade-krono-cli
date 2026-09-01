"""
CLI 维护命令 — 向后兼容薄包装模块。

实际实现已拆分到以下子模块：
  maintenance_status.py  — status
  maintenance_cache.py   — clear_cache / warm_cache
  maintenance_sync.py    — sync_universe / sync_whitelist + _resolve_tickers
  maintenance_history.py — history
  maintenance_eval.py    — eval_prediction
  maintenance_retry.py   — retry_failed
"""

from __future__ import annotations

# 向后兼容导出（原有 import 路径无需修改）
from trade_krono_cli.cli_commands.maintenance_cache import clear_cache, warm_cache
from trade_krono_cli.cli_commands.maintenance_eval import eval_prediction
from trade_krono_cli.cli_commands.maintenance_history import history
from trade_krono_cli.cli_commands.maintenance_retry import retry_failed
from trade_krono_cli.cli_commands.maintenance_status import status
from trade_krono_cli.cli_commands.maintenance_sync import (
    _resolve_tickers,
    rank_providers,
    sync_universe,
    sync_whitelist,
)

__all__ = [
    "_resolve_tickers",
    "clear_cache",
    "eval_prediction",
    "history",
    "rank_providers",
    "retry_failed",
    "status",
    "sync_universe",
    "sync_whitelist",
    "warm_cache",
]
