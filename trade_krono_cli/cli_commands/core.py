"""CLI 核心命令 — 向后兼容包装层。

所有实现已拆分到子模块：
  _core_helpers.py   → 共享工具函数（_load_env / _sanitize_path / _load_tickers 等）
  run.py             → run() 一键并行运行（TA + Kronos）
  ta.py              → ta() 仅 TradingAgents 分析
  kronos.py          → kronos() 仅 Kronos 预测
"""

from __future__ import annotations

# 共享工具函数（cli.py 和测试直接导入）
from trade_krono_cli.cli_commands._core_helpers import (
    _build_degrade_overrides,
    _load_env,
    _load_tickers,
    _sanitize_path,
)

# 命令函数（从子模块重新导出，保持 cli.py 导入路径不变）
from trade_krono_cli.cli_commands.kronos import kronos
from trade_krono_cli.cli_commands.run import run
from trade_krono_cli.cli_commands.ta import ta

__all__ = [
    "run",
    "ta",
    "kronos",
    "_load_env",
    "_load_tickers",
    "_sanitize_path",
    "_build_degrade_overrides",
]
