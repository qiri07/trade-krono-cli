"""fill_missing 选股 — 进度文件读写（断点续传）。

用于多线程场景下安全地记录已完成 / 失败的 ticker，避免中断重启后重复拉取。
"""

from __future__ import annotations

import json
import os
import threading

from scripts.fill_missing.constants import PROGRESS_FILE, PROGRESS_FLUSH_EVERY

# 进度文件读写锁（多线程安全）
_progress_lock = threading.Lock()
# 进度文件写入计数器：每 PROGRESS_FLUSH_EVERY 个 ticker 写盘一次，避免 O(N²) 磁盘 I/O
_progress_write_count = 0


def _load_completed_tickers() -> set[str]:
    """从进度文件加载已成功处理的 ticker 集合。

    用于断点续传：若 ticker 已在进度文件中标记为完成，则跳过重复拉取。

    Returns:
        已完成的 ticker 集合。
    """
    if not PROGRESS_FILE.exists():
        return set()
    try:
        data = json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
        return set(data.get("completed", [])) if isinstance(data, dict) else set()
    except (json.JSONDecodeError, OSError):
        return set()


def _load_failed_tickers() -> set[str]:
    """从进度文件加载标记为失败的 ticker 集合。

    失败的 ticker 会在后续运行中被重试，避免一次失败就永久放弃。

    Returns:
        已标记失败的 ticker 集合。
    """
    if not PROGRESS_FILE.exists():
        return set()
    try:
        data = json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
        return set(data.get("failed", [])) if isinstance(data, dict) else set()
    except (json.JSONDecodeError, OSError):
        return set()


def _save_progress(completed: set[str], failed: set[str]) -> None:
    """将已完成和失败的 ticker 集合原子写入进度文件。

    Args:
        completed: 已完成的 ticker 集合。
        failed: 已标记失败的 ticker 集合。
    """
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = PROGRESS_FILE.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(
            {"completed": sorted(completed), "failed": sorted(failed)},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    os.replace(tmp, PROGRESS_FILE)


def _mark_completed(ticker: str, completed: set[str], failed: set[str]) -> None:
    """将单个 ticker 标记为完成并持久化到进度文件。

    线程安全：内部使用全局锁，可在多线程场景下安全调用。
    每 PROGRESS_FLUSH_EVERY 个 ticker 写盘一次，避免 O(N²) 磁盘 I/O。
    步骤结束时应调用 _flush_completed() 强制写盘。

    Args:
        ticker: 已成功处理的 ticker。
        completed: 当前已完成的 ticker 集合（原地修改）。
        failed: 当前已标记失败的 ticker 集合（原地修改）。
    """
    global _progress_write_count
    with _progress_lock:
        completed.add(ticker)
        failed.discard(ticker)  # 从失败集中移除，避免重复重试
        _progress_write_count += 1
        if _progress_write_count % PROGRESS_FLUSH_EVERY == 0:
            _save_progress(completed, failed)


def _mark_failed(ticker: str, completed: set[str], failed: set[str]) -> None:
    """将单个 ticker 标记为失败并持久化到进度文件。

    失败的 ticker 会在后续运行中被重试。

    Args:
        ticker: 拉取失败的 ticker。
        completed: 当前已完成的 ticker 集合（原地修改）。
        failed: 当前已标记失败的 ticker 集合（原地修改）。
    """
    global _progress_write_count
    with _progress_lock:
        failed.add(ticker)
        completed.discard(ticker)
        _progress_write_count += 1
        if _progress_write_count % PROGRESS_FLUSH_EVERY == 0:
            _save_progress(completed, failed)


def _flush_completed(completed: set[str], failed: set[str]) -> None:
    """强制将当前进度写入磁盘。应在每个步骤结束时调用。

    Args:
        completed: 当前已完成的 ticker 集合。
        failed: 当前已标记失败的 ticker 集合。
    """
    global _progress_write_count
    with _progress_lock:
        _progress_write_count = 0
        _save_progress(completed, failed)
