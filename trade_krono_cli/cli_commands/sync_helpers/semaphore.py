"""sync_helpers.semaphore — 并发信号量管理。

管理 Provider 级别和全局级别的线程信号量，防止 API 限流。
"""

from __future__ import annotations

import threading

# 每个 Provider 的最大并发请求数（防止被限流/拉黑）
_PROVIDER_CONCURRENCY: dict[str, int] = {
    "mootdx": 5,
    "baostock": 3,
    "akshare": 3,
    "tushare": 3,
    "tonghuashun": 3,
}

# 全局请求间隔（秒），用于在批次之间分散请求
_BETWEEN_REQUEST_DELAY: float = 0.1

# 周期性健康检查：每处理 N 只股票或间隔 M 秒重新检测一次 Provider 健康状态
_HEALTH_CHECK_INTERVAL_TICKERS: int = 200
_HEALTH_CHECK_MIN_INTERVAL_SEC: float = 60.0

# 滞环阈值：连续失败多少次才剔除 Provider，连续成功多少次才恢复
_HEALTH_REMOVE_THRESHOLD: int = 3
_HEALTH_RESTORE_THRESHOLD: int = 2

# 模块级：每个 Provider 的并发信号量
_provider_semaphores: dict[str, threading.Semaphore] = {}
_semaphores_lock = threading.Lock()

# 全局并发上限（所有 Provider 合计），防止整体流量过大
_global_semaphore = threading.Semaphore(20)
_global_sem_lock = threading.Lock()


def _get_provider_semaphore(provider: str) -> threading.Semaphore:
    """获取（或创建）指定 Provider 的并发信号量。"""
    with _semaphores_lock:
        if provider not in _provider_semaphores:
            limit = _PROVIDER_CONCURRENCY.get(provider, 3)
            _provider_semaphores[provider] = threading.Semaphore(limit)
        return _provider_semaphores[provider]


def _get_global_semaphore() -> threading.Semaphore:
    """获取全局并发信号量，首次调用时根据可用 Provider 数量动态调整。"""
    with _global_sem_lock:
        return _global_semaphore
