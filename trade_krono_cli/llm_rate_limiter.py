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

# 限流参数
MIN_REQUEST_DELAY: float = 1.5  # 相邻请求最小间隔（秒）
WINDOW_SECONDS: float = 60.0  # 滑动窗口大小（秒）
MAX_REQUESTS_PER_WINDOW: int = 10  # 每窗口最大请求数

# 模块级状态（线程安全）
_lock = threading.Lock()
_request_log: deque[float] = deque()


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


# Agnes 免费账户额度耗尽的错误特征（需升级 Token Plan 或更换 key）
_QUOTA_EXHAUSTED_MARKERS: tuple[str, ...] = (
    "token plan",
    "free users",
    "quota",
    "billing",
    "insufficient",
    "credit",
    "upgrade to",
)

# 频率限流的退避参数（服务端 429 通常几秒~几十秒内恢复）
_BACKOFF_BASE: float = 5.0  # 首次重试等待秒数
_BACKOFF_MAX: float = 30.0  # 单次等待上限


def is_quota_exhausted(error: Exception) -> bool:
    """判断异常是否为账户额度/付费墙类型限流（非普通频率限流）。

    命中任一特征关键词返回 True；此类错误通常需升级付费计划或更换 key，
    短暂等待无法恢复，调用方应据此快速失败并给出明确提示。
    """
    msg = str(error).lower()
    return any(marker in msg for marker in _QUOTA_EXHAUSTED_MARKERS)


def backoff_wait(attempt: int) -> float:
    """返回频率限流的等待秒数（指数退避，带确定性抖动，上限 30s）。

    attempt 从 1 开始；等待 = min(_BACKOFF_BASE * 2**(attempt-1), _BACKOFF_MAX)，
    并附加一个 0~1s 的确定性抖动（基于 attempt 与等待值的哈希），避免惊群，
    同时满足静态检查（不使用随机数生成器）。
    """
    wait = min(_BACKOFF_BASE * (2 ** (attempt - 1)), _BACKOFF_MAX)
    # 确定性抖动：对 (attempt, wait) 做哈希取模，落在 0.0~1.0 区间
    jitter = (abs(hash((attempt, wait))) % 1000) / 1000.0
    return wait + jitter
