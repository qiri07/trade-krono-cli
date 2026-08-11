"""日志配置 — 统一使用 loguru。"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional
from loguru import logger

from trade_krono_cli.config import get_settings


def setup_logger(level: str = "INFO", log_file: Optional[Path] = None) -> None:
    """初始化 loguru 日志。"""
    logger.remove()  # 移除默认 handler

    # 控制台 handler
    logger.add(
        sys.stderr,
        level=level,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
               "<level>{level: <8}</level> | "
               "<cyan>{name}</cyan>:<cyan>{function}</cyan> | "
               "<level>{message}</level>",
        colorize=True,
    )

    # 文件 handler
    if log_file:
        logger.add(
            str(log_file),
            level="DEBUG",
            rotation="10 MB",
            retention="7 days",
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function} | {message}",
        )


# 模块导入时自动初始化
_settings = get_settings()
_log_file = _settings.cache_dir.parent / "pipeline.log"
try:
    setup_logger(log_file=_log_file)
except Exception:
    pass  # 日志初始化失败不影响主流程
