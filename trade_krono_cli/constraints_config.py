"""
A 股交易约束配置（兼容层）。

已从 configs/trading.py 迁移，此处保留别名。
"""

from __future__ import annotations

# 重新导出，保持向后兼容
from trade_krono_cli.configs.trading import ConstraintConfig  # noqa: F401

__all__ = ["ConstraintConfig"]
