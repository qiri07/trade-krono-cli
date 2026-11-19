"""日志配置 — 统一使用 loguru（含 JSON 结构化输出）。

本模块是全项目唯一的日志配置入口，提供两种模式：

1. **生产模式**（默认）：注册三个 handler
     1. 控制台   — 彩色文本，便于人读
     2. pipeline.log  — 文本文件，含完整 DEBUG 日志
     3. pipeline.json   — JSON 结构化文件，供机器解析 / 日志聚合

2. **自定义模式**（显式传 ``json_format`` 或 ``sink``）：注册单个 handler，
   由调用方决定输出为 JSON 还是文本、写到 stderr 还是自定义 sink（测试用）。

JSON 序列化统一走 :func:`_json_line`，两种模式产出的字段完全一致：
``ts / level / module / function / line / message [/ extra]``。
"""

from __future__ import annotations

import json
import sys
import threading
import time
from datetime import timezone
from typing import TYPE_CHECKING

from loguru import logger

from trade_krono_cli.config import Settings, get_settings
from trade_krono_cli.security import sanitize_for_log

if TYPE_CHECKING:
    from pathlib import Path
    from typing import TextIO

    from loguru import Record

# Type stub so mypy knows this attribute exists (set dynamically at runtime).
_json_sink: _JsonLogSink | None = None


class _JsonLogSink:
    """JSON 结构化日志 sink，累积记录供测试访问。"""

    def __init__(self) -> None:
        self.records: list[str] = []

    def write(self, message: str) -> None:
        self.records.append(message.strip())
        if not getattr(self, "_test_mode", False):
            sys.stderr.write(message)
            sys.stderr.flush()


def _json_line(record: dict) -> str:
    """将 loguru record（``record`` dict）序列化为单行 JSON。

    生产 pipeline.json 与自定义 JSON 模式共用此函数，保证字段一致。
    """
    # record["time"] 为带时区的本地时间，统一转成 UTC 再加 Z，避免把本地时间标成 UTC
    entry = {
        "ts": record["time"].astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "level": record["level"].name,
        "module": record["name"],
        "function": record["function"],
        "line": record["line"],
        "message": record["message"],
    }
    extra = {k: v for k, v in record["extra"].items() if k != "task_id"}
    if extra:
        entry["extra"] = extra
    return json.dumps(entry, ensure_ascii=False)


def _make_json_handler(json_file: str):
    """返回 JSON 日志 handler，逐行追加写入指定文件。"""

    def json_handler(record) -> None:
        # record 是 loguru._handler.Message 对象，.record 是原始 dict
        with open(json_file, "a", encoding="utf-8") as f:
            f.write(_json_line(record.record) + "\n")

    return json_handler


class _RotatingJsonFileSink:
    """按大小轮转 + 按时间删除的 JSON 行日志 sink，供生产模式 ``pipeline.json`` 使用。

    背景：loguru 的 ``format`` 回调返回值会被当作格式模板再次 ``format_map``
    （JSON 里的 ``{ts}`` 会被当成占位符，抛 ``KeyError: '"ts"'``），无法直接
    输出 JSON；而 callable sink 又不接受 ``rotation`` / ``retention`` 参数。
    因此自带两套清理，对齐原 loguru 配置（``rotation="10 MB"`` + ``retention="7 days"``）：

    1. **按大小轮转**：超过 ``max_bytes`` 时把当前文件顺移为 ``.1`` / ``.2`` ...，
       最多保留 ``retention`` 份（超出即删除最旧的一份）。
    2. **按时间删除**：轮转后清扫 mtime 早于 ``max_age_days`` 天的已轮转文件，
       避免低量日志长期堆积、或高量日志把 7 天前记录过早挤掉。

    loguru 以 ``enqueue=True`` 调用 sink，写入已串行化；仍加锁以防外部直接调用。
    """

    def __init__(
        self,
        path: Path,
        max_bytes: int = 10 * 1024 * 1024,
        retention: int = 7,
        max_age_days: int = 7,
    ) -> None:
        self._path = path
        self._max_bytes = max_bytes
        self._retention = retention
        self._max_age_days = max_age_days
        self._lock = threading.Lock()

    def __call__(self, record) -> None:
        """写一条 JSON 日志（``record`` 为 loguru Message 对象）。"""
        line = _json_line(record.record) + "\n"
        with self._lock:
            self._rotate_if_needed()
            self._delete_aged()
            with self._path.open("a", encoding="utf-8") as f:
                f.write(line)

    def _rotate_if_needed(self) -> None:
        """当前文件超过 ``max_bytes`` 时顺移为 ``.1``，旧文件依次后移。"""
        if not self._path.exists() or self._path.stat().st_size < self._max_bytes:
            return
        for i in range(self._retention - 1, 0, -1):
            src = self._path.with_name(f"{self._path.name}.{i}")
            dst = self._path.with_name(f"{self._path.name}.{i + 1}")
            if src.exists():
                src.replace(dst)
        self._path.replace(self._path.with_name(f"{self._path.name}.1"))

    def _delete_aged(self) -> None:
        """删除 mtime 早于 ``max_age_days`` 天的已轮转文件（当前文件永不删）。"""
        if self._max_age_days <= 0:
            return
        cutoff = time.time() - self._max_age_days * 86400
        for i in range(1, self._retention + 1):
            rotated = self._path.with_name(f"{self._path.name}.{i}")
            if rotated.exists() and rotated.stat().st_mtime < cutoff:
                rotated.unlink()


def setup_logger(
    level: str = "INFO",
    log_file: Path | None = None,
    settings: Settings | None = None,
    *,
    json_format: bool = False,
    sink: TextIO | None = None,
) -> None:
    """初始化 loguru 日志。

    Parameters
    ----------
    level : 日志级别（默认 INFO）。
    log_file : 文件路径（可选；生产模式下缺省为 ``outputs/pipeline.log``）。
    settings : Settings 实例；生产模式下用于定位 ``outputs/`` 目录。
    json_format : 为 ``True`` 时走 JSON 单 handler 模式（与 ``sink`` 二选一，测试/机器消费用）。
    sink : 自定义输出目标（测试用，不传则写 stderr）。传入即触发自定义模式。

    Notes
    -----
    ``json_format`` / ``sink`` 任一被显式使用即进入自定义模式（单 handler）；
    二者都不传时进入生产模式（控制台 + pipeline.log + pipeline.json 三 handler）。

    """
    logger.remove()  # 移除所有默认 handler
    # 全局脱敏：任何 handler 写入前先过滤 API Key / Bearer Token / x-api-key
    def _redact_patcher(record: Record) -> None:
        try:
            record["message"] = sanitize_for_log(record["message"])
        except (TypeError, KeyError):  # 非字符串消息不应阻断日志
            return

    logger.configure(patcher=_redact_patcher)

    text_fmt = (
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan> | "
        "<level>{message}</level>"
    )

    if json_format:
        json_sink = _JsonLogSink()
        global _json_sink
        _json_sink = json_sink

        def _json_handler(record) -> None:
            # record 是 loguru._handler.Message 对象，.record 是原始 dict
            json_str = _json_line(record.record)
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

    # ── 自定义模式的可选文件 handler ───────────────────────────────────────
    if log_file is not None and (json_format or sink is not None):
        logger.add(
            str(log_file),
            level="DEBUG",
            format=text_fmt,
            rotation="10 MB",
            retention="7 days",
            enqueue=False,
        )
        return

    if json_format or sink is not None:
        return

    # ── 生产模式：pipeline.log 文本文件（DEBUG 级别，保留全量）──────────────
    s = settings or get_settings()
    if log_file is None:
        log_file = s.cache_dir.parent / "pipeline.log"
    logger.add(
        str(log_file),
        level="DEBUG",
        rotation="10 MB",
        retention="7 days",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function} | {message}",
    )

    # ── 生产模式：pipeline.json 结构化 JSON 文件（INFO 级别，供机器解析）───
    json_file = s.cache_dir.parent / "pipeline.json"
    logger.add(
        _RotatingJsonFileSink(json_file),
        level="INFO",
        colorize=False,
        enqueue=True,
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
