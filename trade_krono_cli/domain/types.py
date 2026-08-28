"""
Domain Types — 基础枚举和类型定义。

独立于其他 domain 模块，避免循环导入。
"""

from __future__ import annotations

from enum import Enum


class Signal(str, Enum):
    """交易信号。"""

    BUY = "BUY"
    OVERWEIGHT = "OVERWEIGHT"
    HOLD = "HOLD"
    SELL = "SELL"


class Direction(str, Enum):
    """价格方向。"""

    UP = "UP"
    DOWN = "DOWN"
    FLAT = "FLAT"

    @classmethod
    def from_str(cls, value: str | None) -> "Direction | None":
        if value is None:
            return None
        try:
            return cls(value.upper())
        except ValueError:
            return None


class ExperimentType(str, Enum):
    """实验类型。"""

    ALPHA = "alpha"
    MODEL = "model"
    CONFIG = "config"
    DATA = "data"
    HYPOTHESIS = "hypothesis"


__all__ = ["Signal", "Direction", "ExperimentType"]
