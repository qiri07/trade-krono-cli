"""
结构化日志配置。

支持两种模式：
  - text   : 人类可读（默认，保留现有格式）
  - json   : JSON 结构化输出，可被 logstash/ELK/jq 消费
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

from loguru import logger

# Type stub so mypy knows this attribute exists (set dynamically at runtime).
_json_sink: Optional[_JsonLogSink] = None


class _JsonLogSink:
    """JSON 结构化日志 sink，累积记录供测试访问。"""

    def __init__(self):
        self.records: list[str] = []

    def write(self, message: str) -> None:
        self.records.append(message.strip())
        if not getattr(self, '_test_mode', False):
            sys.stderr.write(message)
            sys.stderr.flush()


def _json_format(record: dict) -> str:
    """将 loguru record 序列化为单行 JSON，供 _JsonLogSink 使用。"""
    entry = {
        "ts": record["time"].strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "level": record["level"].name,
        "module": record["name"],
        "function": record["function"],
        "line": record["line"],
        "message": record["message"],
    }
    extra = {k: v for k, v in record["extra"].items() if k != "task_id"}
    if extra:
        entry["extra"] = extra
    return json.dumps(entry, ensure_ascii=False) + "\n"


def setup_logger(
    level: str = "INFO",
    log_file: Optional[Path] = None,
    json_format: bool = False,
    sink=None,
) -> None:
    """
    初始化 loguru 日志。

    Parameters
    ----------
    level : 日志级别
    log_file : 文件路径（可选）
    json_format : 是否输出 JSON 结构化日志
    sink : 自定义输出目标（测试用，不传则写 stderr）
    """
    logger.remove()  # 移除所有默认 handler

    text_fmt = (
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan> | "
        "<level>{message}</level>"
    )

    if json_format:
        json_sink = _JsonLogSink()
        import trade_krono_cli.logging_config as _mod
        _mod._json_sink = json_sink

        def _json_handler(record):
            # record 是 loguru._handler.Message 对象，.record 是原始 dict
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
            json_str = json.dumps(entry, ensure_ascii=False)
            json_sink.records.append(json_str)
            if sink is None:
                sys.stderr.write(json_str + "\n")
                sys.stderr.flush()

        logger.add(
            _json_handler,
            level=level,
            format="",  # 空 format → handler 收到 Message 对象而非字符串
            colorize=False,
            enqueue=False,
        )
    else:
        target = sink if sink is not None else sys.stderr
        logger.add(
            target,
            level=level,
            format=text_fmt,
            colorize=sink is None,
            enqueue=False,
        )

    # 文件 handler
    if log_file:
        logger.add(
            str(log_file),
            level="DEBUG",
            format=text_fmt,
            rotation="10 MB",
            retention="7 days",
            enqueue=False,
        )


def info_structured(message: str, **extra) -> None:
    """结构化 INFO 日志，extra 字段会合并到 JSON 输出中。"""
    logger.info(message, extra=extra)


def error_structured(message: str, **extra) -> None:
    """结构化 ERROR 日志。"""
    logger.error(message, extra=extra)


def warning_structured(message: str, **extra) -> None:
    """结构化 WARNING 日志。"""
    logger.warning(message, extra=extra)
