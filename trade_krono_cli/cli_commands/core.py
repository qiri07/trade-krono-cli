"""CLI 核心命令 — 向后兼容包装层。

所有实现已拆分到子模块：
  _core_helpers.py   → 共享工具函数（_load_env / _sanitize_path / _load_tickers 等）
  _core_commands.py  → run / ta / kronos CLI 命令
"""

from __future__ import annotations

# 共享工具函数（cli.py 和测试直接导入）
from trade_krono_cli.cli_commands._core_commands import kronos, run, ta
from trade_krono_cli.cli_commands._core_helpers import (
    _build_degrade_overrides,
    _build_retry_overrides,
    _load_env,
    _load_tickers,
    _sanitize_path,
)

__all__ = [
    "run",
    "ta",
    "kronos",
    "_load_env",
    "_load_tickers",
    "_sanitize_path",
    "_build_degrade_overrides",
    "_build_retry_overrides",
]
