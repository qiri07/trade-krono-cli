"""Agnes AI API 全局限流器。

Agnes 免费版限制：
- 60 秒内最多 10 次请求
- 相邻请求最小间隔 1.5 秒

本模块提供线程安全的滑动窗口限流，所有调用 Agnes API 的脚本都应使用此模块。
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Deque

# 限流参数
MIN_REQUEST_DELAY: float = 1.5  # 相邻请求最小间隔（秒）
WINDOW_SECONDS: float = 60.0  # 滑动窗口大小（秒）
MAX_REQUESTS_PER_WINDOW: int = 10  # 每窗口最大请求数

# 模块级状态（线程安全）
_lock = threading.Lock()
_request_log: Deque[float] = deque()


def wait_for_rate_limit() -> None:
    """等待至满足限流条件。

    规则1：60秒内请求数 < 10
    规则2：距上次请求 >= 1.5秒

    此函数必须在每次调用 Agnes API 前调用。
    """
    with _lock:
        now = time.monotonic()

        # 清理窗口外的记录
        cutoff = now - WINDOW_SECONDS
        while _request_log and _request_log[0] <= cutoff:
            _request_log.popleft()

        # 规则1：检查窗口内请求数
        if len(_request_log) >= MAX_REQUESTS_PER_WINDOW:
            # 等待最早的一个请求滑出窗口
            oldest = _request_log[0]
            wait_time = oldest + WINDOW_SECONDS - now
            if wait_time > 0:
                time.sleep(wait_time)
                # 重新清理
                now = time.monotonic()
                cutoff = now - WINDOW_SECONDS
                while _request_log and _request_log[0] <= cutoff:
                    _request_log.popleft()

        # 规则2：检查最小间隔
        if _request_log:
            elapsed = now - _request_log[-1]
            if elapsed < MIN_REQUEST_DELAY:
                time.sleep(MIN_REQUEST_DELAY - elapsed)
                now = time.monotonic()

        # 记录本次请求
        _request_log.append(time.monotonic())


def get_request_stats() -> dict[str, int | float]:
    """返回当前限流统计信息（用于调试）。"""
    with _lock:
        now = time.monotonic()
        cutoff = now - WINDOW_SECONDS
        recent = [t for t in _request_log if t > cutoff]
        return {
            "requests_in_window": len(recent),
            "max_per_window": MAX_REQUESTS_PER_WINDOW,
            "min_delay": MIN_REQUEST_DELAY,
            "window_seconds": WINDOW_SECONDS,
        }


def reset_rate_limit() -> None:
    """重置限流状态（用于测试）。"""
    with _lock:
        _request_log.clear()
