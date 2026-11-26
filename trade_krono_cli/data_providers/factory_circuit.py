"""trade_krono_cli.data_providers.factory_circuit — Provider 健康检查与连续失败熔断。

职责（单一职责原则）：
  · Provider 健康检查（带短 TTL 缓存，避免全市场数千只票成倍放大 API 流量）
  · 连续失败熔断：连续失败达阈值后，在 TTL 内标记为“暂时不可用”，跳过探测与调用
  · 成功调用后重置失败计数
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from trade_krono_cli.data_providers.base import DataProvider


class ProviderCircuitBreaker:
    """Provider 健康检查与连续失败熔断器。

    设计要点：
      - 健康检查结果短 TTL 缓存（60秒），避免重复探测
      - 连续失败达到阈值（3次）后进入 300s 冷却期，期间直接降级
      - 成功调用后重置失败计数，恢复可用
    """

    # 连续失败阈值：达到后该 provider 在 UNAVAILABLE_TTL_SEC 内被标记为不可用
    _PROVIDER_FAIL_THRESHOLD = 3
    # 标记不可用后的冷却时间（秒），期间直接降级，不再探测
    _PROVIDER_UNAVAILABLE_TTL_SEC = 300.0

    def __init__(self) -> None:
        # Provider 连续失败计数与"暂时不可用"标记
        self._provider_fail_count: dict[str, int] = {}
        self._provider_unavailable_until: dict[str, float] = {}
        self._provider_fail_lock = threading.Lock()

        # 健康检查结果短 TTL 缓存：fetch_quote/fetch_metadata 逐票调用 health_check，
        # 全市场数千只票会成倍放大 API 流量，这里按 Provider 缓存 60 秒。
        self._health_cache: dict[str, tuple[bool, float]] = {}
        self._health_lock = threading.Lock()

    def _provider_fail_threshold(self) -> int:
        return self._PROVIDER_FAIL_THRESHOLD

    def _provider_unavailable_ttl_sec(self) -> float:
        return self._PROVIDER_UNAVAILABLE_TTL_SEC

    def is_provider_healthy(
        self, name: str, provider: DataProvider, health_ttl_sec: float = 60.0
    ) -> bool:
        """带短 TTL 缓存和连续失败熔断的 Provider 健康检查。

        如果某 Provider 连续失败达到阈值（3 次），在 5 分钟内标记为“暂时不可用”，
        期间跳过 health_check 和实际调用，直接降级到下一个 Provider。
        成功调用后重置失败计数，恢复可用状态。

        这避免对长期不可达的数据源反复发起无效探测请求。

        Args:
            name: Provider 名称（用于熔断计数与日志）。
            provider: Provider 实例。
            health_ttl_sec: 健康检查缓存 TTL（秒）。

        Returns:
            True 表示 Provider 当前健康可用；False 表示不可用（含冷却期）。
        """
        ttl = health_ttl_sec
        now = time.monotonic()

        # 检查是否在“暂时不可用”冷却期内
        with self._provider_fail_lock:
            unavailable_until = self._provider_unavailable_until.get(name, 0.0)
            if now < unavailable_until:
                return False
            fail_count = self._provider_fail_count.get(name, 0)

        # 检查健康检查缓存
        with self._health_lock:
            cached = self._health_cache.get(name)
            if cached is not None and now - cached[1] < ttl:
                return cached[0]

        try:
            ok = bool(provider.health_check())
        except Exception as e:  # noqa: BLE001 - provider 自定义实现，异常类型不可控
            logger.debug(f"{name} health_check 异常: {e}")
            ok = False

        with self._health_lock:
            self._health_cache[name] = (ok, now)

        if ok:
            # 成功：重置失败计数
            with self._provider_fail_lock:
                self._provider_fail_count.pop(name, None)
                self._provider_unavailable_until.pop(name, None)
        else:
            # 失败：增加计数，达到阈值后进入冷却期
            with self._provider_fail_lock:
                new_count = fail_count + 1
                self._provider_fail_count[name] = new_count
                if new_count >= self._PROVIDER_FAIL_THRESHOLD:
                    self._provider_unavailable_until[name] = (
                        now + self._PROVIDER_UNAVAILABLE_TTL_SEC
                    )
                    logger.warning(
                        f"⚠️  {name} 连续失败 {new_count} 次，进入 {self._PROVIDER_UNAVAILABLE_TTL_SEC:.0f}s 冷却期，将跳过探测"
                    )
        return ok

    def record_provider_fail(self, name: str) -> None:
        """记录一次 Provider 调用失败，用于熔断计数。"""
        now = time.monotonic()
        with self._provider_fail_lock:
            fail_count = self._provider_fail_count.get(name, 0) + 1
            self._provider_fail_count[name] = fail_count
            if fail_count >= self._PROVIDER_FAIL_THRESHOLD:
                self._provider_unavailable_until[name] = (
                    now + self._PROVIDER_UNAVAILABLE_TTL_SEC
                )
                logger.warning(
                    f"⚠️  {name} 连续失败 {fail_count} 次，进入 {self._PROVIDER_UNAVAILABLE_TTL_SEC:.0f}s 冷却期"
                )

    def record_provider_success(self, name: str) -> None:
        """记录一次 Provider 调用成功，重置熔断计数。"""
        with self._provider_fail_lock:
            self._provider_fail_count.pop(name, None)
            self._provider_unavailable_until.pop(name, None)

    def reset(self) -> None:
        """清空所有熔断与健康检查状态（用于测试隔离）。"""
        with self._provider_fail_lock:
            self._provider_fail_count.clear()
            self._provider_unavailable_until.clear()
        with self._health_lock:
            self._health_cache.clear()


# 向后兼容：保留模块级单例供原工厂直接引用
_default_circuit: ProviderCircuitBreaker | None = None
_default_circuit_lock = threading.Lock()


def get_default_circuit() -> ProviderCircuitBreaker:
    """获取默认的全局熔断器单例。"""
    global _default_circuit
    if _default_circuit is None:
        with _default_circuit_lock:
            if _default_circuit is None:
                _default_circuit = ProviderCircuitBreaker()
    return _default_circuit
