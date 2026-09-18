"""CLI 核心命令 — 向后兼容包装层。

实现已拆分到子模块：
  run.py   → run()          一键并行运行（TA + Kronos）
  ta.py    → ta()           仅 TradingAgents 分析
  kronos.py → kronos()      仅 Kronos 预测

此处重新导出，保持原有导入路径不变。
"""

from __future__ import annotations

from trade_krono_cli.cli_commands.kronos import kronos  # noqa: F401
from trade_krono_cli.cli_commands.run import run  # noqa: F401
from trade_krono_cli.cli_commands.ta import ta  # noqa: F401

__all__ = ["run", "ta", "kronos"]
