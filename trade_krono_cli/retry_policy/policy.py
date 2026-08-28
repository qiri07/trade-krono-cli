"""
retry_policy.policy — RetryPolicy 配置 + 智能重试装饰器。
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from functools import wraps
from typing import Any, Callable, Optional, TypeVar, Union

from loguru import logger

from trade_krono_cli.retry_policy.classifier import classify_error
from trade_krono_cli.retry_policy.exceptions import (
    RateLimitError,
    TradeKronoNonRetryableError,
)

F = TypeVar("F", bound=Callable[..., Any])


@dataclass
class RetryPolicy:
    """
    重试策略配置。

    Attributes
    ----------
    max_attempts       : 最大尝试次数（含首次），仅对 retriable 错误生效
    base_delay         : 初始退避秒数
    jitter             : 是否添加随机抖动（0.0–1.0），防止 thundering herd
    rate_limit_backoff : 限流时是否启用自适应退避（解析 Retry-After 头）
    rate_limit_max_wait: 限流自适应退避上限（秒）
    skip_non_retriable : 是否跳过不可重试错误（默认 True，非 retriable 直接抛）
    """

    max_attempts: int = 3
    base_delay: float = 2.0
    jitter: bool = True
    rate_limit_backoff: bool = True
    rate_limit_max_wait: float = 60.0
    skip_non_retriable: bool = True

    # 各错误类型的覆盖配置（可选）
    network_attempts: Optional[int] = None
    network_delay: Optional[float] = None
    rate_limit_attempts: Optional[int] = None
    rate_limit_delay: Optional[float] = None


def smart_retry(
    policy: Optional[Union[RetryPolicy, F]] = None,
) -> Union[Callable[[F], F], F]:
    """
    智能重试装饰器：根据错误类型差异化退避。

    支持两种用法：
      @smart_retry                     # 使用默认策略
      @smart_retry(RetryPolicy(...))    # 使用指定策略

    行为：
      · retriable 错误：指数退避（+ 可选抖动），最多 max_attempts 次
      · rate limit 错误：若 enable_rate_limit_backoff=True，
        优先使用 Retry-After 头，否则回退到指数退避
      · non_retriable 错误：直接抛出（skip_non_retriable=True 时）
    """
    # 处理 @smart_retry（无括号）的情况：第一个参数是函数本身
    if callable(policy) and not isinstance(policy, RetryPolicy):
        fn = policy  # type: ignore[assignment]
        policy = RetryPolicy()
        return _make_smart_retry_decorator(fn, policy)

    actual_policy: RetryPolicy = policy if policy is not None else RetryPolicy()

    def decorator(fn: F) -> F:
        return _make_smart_retry_decorator(fn, actual_policy)

    return decorator


def _make_smart_retry_decorator(fn: F, policy: RetryPolicy) -> F:
    """内部：创建带有指定策略的重试装饰器。"""

    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        last_exc: Optional[Exception] = None

        for attempt in range(1, policy.max_attempts + 1):
            try:
                return fn(*args, **kwargs)
            except TradeKronoNonRetryableError as e:
                logger.warning(f"❌ [{fn.__name__}] 不可重试错误（放弃）: {e}")
                raise

            except Exception as e:
                last_exc = e
                category, desc = classify_error(e)

                if category == "non_retriable" and policy.skip_non_retriable:
                    logger.warning(f"❌ [{fn.__name__}] 不可重试错误（放弃）: {desc}")
                    raise

                if attempt >= policy.max_attempts:
                    logger.error(
                        f"❌ [{fn.__name__}] "
                        f"第 {attempt}/{policy.max_attempts} 次尝试仍失败 [{category}]: {desc}"
                    )
                    break

                delay = _compute_delay(e, attempt, policy)
                logger.warning(
                    f"⚠️  [{fn.__name__}] "
                    f"第 {attempt}/{policy.max_attempts} 次失败 [{category}]，"
                    f"{delay:.1f}s 后重试... ({desc[:80]})"
                )
                time.sleep(delay)

        raise last_exc  # type: ignore[misc]

    return wrapper  # type: ignore[return-value]


def _compute_delay(exc: Exception, attempt: int, policy: RetryPolicy) -> float:
    """
    根据异常类型和策略计算退避时间。

    - RateLimitError：优先使用 Retry-After 头（若配置了自适应退避）
    - 其他 retriable：指数退避 + 可选抖动
    """
    # 限流自适应退避
    if isinstance(exc, RateLimitError) and policy.rate_limit_backoff:
        if exc.retry_after is not None and exc.retry_after > 0:
            wait = min(float(exc.retry_after), policy.rate_limit_max_wait)
            logger.debug(f"🔄 限流退避：Retry-After={exc.retry_after}s，等待 {wait:.1f}s")
            return wait
        # 无 Retry-After 头时回退到指数退避
        return _exp_backoff(attempt, policy.rate_limit_delay or policy.base_delay, policy.jitter)

    # 普通网络/服务端错误：指数退背
    return _exp_backoff(attempt, policy.base_delay, policy.jitter)


def _exp_backoff(attempt: int, base_delay: float, jitter: bool) -> float:
    """指数退避 + 可选抖动。"""
    delay = base_delay * (2 ** (attempt - 1))
    if jitter:
        delay = delay * (0.5 + 0.5 * random.random())  # [0.5, 1.0] 随机因子
    return delay
