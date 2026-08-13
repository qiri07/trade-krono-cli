"""日志配置。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=False)
class LoggingConfig:
    """日志输出配置。"""

    log_level: str = "INFO"
    log_json: bool = False

    def merge(self, **overrides) -> "LoggingConfig":
        current = {k: getattr(self, k) for k in self.__dataclass_fields__}
        current.update(overrides)
        return LoggingConfig(**current)
