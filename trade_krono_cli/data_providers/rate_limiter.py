"""统一限流管理器 - 所有数据源共享，避免触发 API 限流

使用滑动窗口算法，确保全局请求频率不超过设定阈值，
避免触发服务端 429 限流。

设计要点：
- 全局单例，所有数据源共享同一计数器
- 按 provider 维护独立的请求时间戳队列
- 支持每秒最大请求数和最小请求间隔双重限制
- 支持 429 响应后的冷却期
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass


@dataclass
class RateLimitConfig:
    """限流配置"""

    max_requests_per_second: int = 2        # 每秒最大请求数
    min_interval_seconds: float = 0.5       # 最小请求间隔(秒)
    burst_size: int = 10                    # 突发请求容量
    cooldown_seconds: float = 60.0          # 触发 429 后冷却时间(秒)
    max_retries: int = 5                    # 最大重试次数
    base_backoff: float = 2.0               # 指数退避基数(秒)
    max_backoff: float = 300.0              # 最大退避时间(秒)


class RateLimiter:
    """
    全局速率限制器 - 所有数据源共享

    使用滑动窗口算法，确保全局请求频率不超过设定阈值，
    避免触发服务端 429 限流。

    设计要点：
    - 单例模式，所有 Provider 共享同一计数器
    - 按 Provider 维护独立的请求时间戳队列
    - 双重限制：每秒最大请求数 + 最小请求间隔
    - 支持 429 响应后的冷却期
    """

    _instance: RateLimiter | None = None
    _lock: threading.Lock = threading.Lock()

    def __init__(self, config: RateLimitConfig | None = None):
        self.config = config or RateLimitConfig()
        self._request_log: deque[float] = deque()
        self._lock = threading.Lock()
        # 按 Provider 维护独立的请求时间戳
        self._provider_logs: dict[str, deque[float]] = {}
        self._provider_locks: dict[str, float] = {}
        # 按 Provider 维护冷却时间（触发 429 后）
        self._cooldown_until: dict[str, float] = {}
        # 统计信息
        self._total_requests = 0
        self._total_throttled = 0
        self._total_429s = 0

    @classmethod
    def get_instance(cls) -> RateLimiter:
        """获取单例实例"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = RateLimiter()
        return cls._instance

    def acquire(self, provider: str = "default") -> float:
        """
        获取请求许可，返回需要等待的秒数(0.0 表示可立即请求)

        调用方应在请求前调用此方法，根据返回值决定是否等待。

        Args:
            provider: Provider 名称，用于区分不同数据源的计数

        Returns:
            需要等待的秒数，0.0 表示可立即请求
        """
        now = time.monotonic()

        with self._lock:
            # 检查是否在冷却期(刚触发 429 后)
            cooldown_until = self._cooldown_until.get(provider, 0.0)
            if now < cooldown_until:
                return cooldown_until - now

            # 获取该 Provider 的请求时间戳队列
            if provider not in self._provider_logs:
                self._provider_logs[provider] = deque()
            log = self._provider_logs[provider]

            # 清理 1 秒窗口外的记录
            cutoff = now - 1.0
            while log and log[0] <= cutoff:
                log.popleft()

            # 规则 1: 每秒最大请求数
            if self.config.max_requests_per_second > 0 and len(log) >= self.config.max_requests_per_second:
                oldest = log[0]
                wait_time = oldest + 1.0 - now
                if wait_time > 0:
                    return wait_time

            # 规则 2: 最小请求间隔
            if self.config.min_interval_seconds > 0:
                last_request = self._provider_locks.get(provider, 0.0)
                elapsed = now - last_request
                if elapsed < self.config.min_interval_seconds:
                    return self.config.min_interval_seconds - elapsed

            # 记录请求时间
            log.append(now)
            self._provider_locks[provider] = now
            self._total_requests += 1
            return 0.0

    def record_429(self, provider: str = "default") -> None:
        """记录 429 限流错误，触发冷却期

        调用后，该 Provider 在冷却期内的 acquire 都会返回等待时间。
        """
        with self._lock:
            self._cooldown_until[provider] = time.monotonic() + self.config.cooldown_seconds
            self._total_429s += 1
            self._total_throttled += 1

    def record_success(self, provider: str = "default") -> None:
        """记录成功请求，可选地缩短冷却时间"""
        with self._lock:
            # 成功请求后，可适当缩短冷却时间(可选优化)
            pass

    def get_stats(self) -> dict:
        """获取限流统计信息"""
        with self._lock:
            cooldown_remaining = {
                k: max(0.0, v - time.monotonic())
                for k, v in self._cooldown_until.items()
                if v > time.monotonic()
            }
            return {
                "total_requests": self._total_requests,
                "total_throttled": self._total_throttled,
                "total_429s": self._total_429s,
                "cooldown_providers": cooldown_remaining,
                "config": {
                    "max_requests_per_second": self.config.max_requests_per_second,
                    "min_interval_seconds": self.config.min_interval_seconds,
                    "burst_size": self.config.burst_size,
                    "cooldown_seconds": self.config.cooldown_seconds,
                    "max_retries": self.config.max_retries,
                    "base_backoff": self.config.base_backoff,
                    "max_backoff": self.config.max_backoff,
                },
            }


# 全局单例访问函数
def get_rate_limiter() -> RateLimiter:
    """获取全局限流器单例"""
    return RateLimiter.get_instance()
