"""日志配置 — 统一使用 loguru。"""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING

from loguru import logger

from trade_krono_cli.config import Settings, get_settings

if TYPE_CHECKING:
    from pathlib import Path


def _make_json_handler(json_file: str):
    """返回 JSON 日志 handler，写入指定文件。"""

    def json_handler(record) -> None:
        r = record.record
        entry = {
            "ts": r["time"].strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "level": r["level"].name,
            "module": r["name"],
            "function": r["function"],
            "line": r["line"],
            "message": r["message"],
        }
        extra = {k: v for k, v in r["extra"].items() if k != "task_id"}
        if extra:
            entry["extra"] = extra
        with open(json_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    return json_handler


def setup_logger(
    level: str = "INFO",
    log_file: Path | None = None,
    settings: Settings | None = None,
) -> None:
    """初始化 loguru 日志。

    注册三个 handler：
      1. 控制台   — 彩色文本，便于人读
      2. pipeline.log  — 文本文件，含完整 DEBUG 日志
      3. pipeline.json   — JSON 结构化文件，供机器解析 / 日志聚合
    """
    logger.remove()  # 移除默认 handler
    s = settings or get_settings()

    # ── 控制台：彩色文本 ─────────────────────────────────────────────────────
    logger.add(
        sys.stderr,
        level=level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan> | "
            "<level>{message}</level>"
        ),
        colorize=True,
    )

    # ── pipeline.log：文本文件（DEBUG 级别，保留全量）────────────────────────
    if log_file is None:
        log_file = s.cache_dir.parent / "pipeline.log"
    logger.add(
        str(log_file),
        level="DEBUG",
        rotation="10 MB",
        retention="7 days",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function} | {message}",
    )

    # ── pipeline.json：结构化 JSON 文件（INFO 级别，供机器解析）──────────────
    json_file = str(s.cache_dir.parent / "pipeline.json")
    logger.add(
        _make_json_handler(json_file),
        level="INFO",
        enqueue=True,
    )
