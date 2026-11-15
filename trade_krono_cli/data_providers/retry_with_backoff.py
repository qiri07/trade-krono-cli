"""带指数退避的重试装饰器

为数据源拉取提供指数退避重试机制，避免频繁失败时频繁请求触发限流。

设计要点：
- 指数退避：delay = min(base_delay * 2^attempt, max_delay)
- 可选抖动：避免多个请求同时重试造成惊群效应
- 支持自定义可重试异常类型
- 支持回调函数，在重试前执行自定义逻辑(如记录日志)
"""

from __future__ import annotations

import contextlib
import functools
import logging
import random
import time
from typing import TYPE_CHECKING, Any, TypeVar, cast

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

F = TypeVar("F")


def retry_with_backoff(
    max_retries: int = 5,
    base_delay: float = 2.0,
    max_delay: float = 300.0,
    jitter: bool = True,
    retry_on: tuple[type[Exception], ...] = (Exception,),
    on_retry: Callable[[int, Exception], None] | None = None,
) -> Callable[[F], F]:
    """
    带指数退避的重试装饰器

    指数退避公式: delay = min(base_delay * 2^attempt, max_delay)
    可选加入抖动：0~1秒随机值，避免多个请求同时重试造成惊群效应。

    Parameters
    ----------
    max_retries : int
        最大重试次数，默认 5 次
    base_delay : float
        基础延迟秒数，默认 2.0 秒
    max_delay : float
        最大延迟秒数，默认 300.0 秒(5分钟)
    jitter : bool
        是否添加随机抖动(0~1秒随机值)，避免惊群效应，默认 True
    retry_on : tuple[type[Exception], ...]
        需要重试的异常类型，默认 (Exception,)
    on_retry : callable
        重试前的回调函数，签名为 on_retry(attempt: int, exception: Exception)
        用于在重试前记录日志或执行其他操作

    Returns
    -------
    Callable[[F], F]
        装饰器

    Usage
    -----
    @retry_with_backoff(max_retries=5, base_delay=2.0, max_delay=300.0)
    def fetch_data(self, ticker: str) -> Any:
        ...
    """

    def decorator(func: F) -> F:
        f = cast("Any", func)
        @functools.wraps(cast("Any", func))
        def wrapper(*args, **kwargs):
            last_exception: Exception | None = None

            for attempt in range(max_retries + 1):
                try:
                    return f(*args, **kwargs)
                except retry_on as e:
                    last_exception = e

                    # 最后一次重试也失败了
                    if attempt == max_retries:
                        logger.error(
                            f"❌ {f.__name__} 重试{max_retries}次后仍失败: {e}"
                        )
                        raise e

                    # 计算退避时间: delay = min(base_delay * 2^attempt, max_delay)
                    delay = min(base_delay * (2**attempt), max_delay)

                    # 添加抖动：0~1秒随机值，避免多个请求同时重试
                    if jitter:
                        delay += random.uniform(0, 1)  # noqa: S311 - 用于抖动，非安全敏感场景

                    logger.warning(
                        f"⚠️  {f.__name__} 第{attempt + 1}次失败: {e}, "
                        f"{delay:.1f}s后重试({attempt + 1}/{max_retries})"
                    )

                    # 调用重试回调
                    if on_retry:
                        with contextlib.suppress(Exception):
                            on_retry(attempt + 1, e)
                    time.sleep(delay)

            # 不应到达这里，因为最后一次失败会抛出异常
            if last_exception is None:
                raise RuntimeError("unreachable")  # type: ignore[unreachable]
            raise last_exception  # type: ignore[unreachable]

        return wrapper  # type: ignore[return-value]
    return decorator


def retry_kline_fetch(
    max_retries: int = 5,
    base_delay: float = 5.0,
    max_delay: float = 300.0,
    jitter: bool = True,
) -> Callable[[F], F]:
    """
    专门用于 K 线拉取的重试装饰器，使用更保守的参数。

    专门用于数据源 K 线拉取，考虑到数据源对频率限制较敏感，
    使用更保守的重试参数。

    Parameters
    ----------
    max_retries : int
        最大重试次数，默认 5 次
    base_delay : float
        基础延迟秒数，默认 5.0 秒
    max_delay : float
        最大延迟秒数，默认 300.0 秒(5分钟)
    jitter : bool
        是否添加随机抖动，默认 True

    Returns
    -------
    Callable[[F], F]
        装饰器

    Usage
    -----
    @retry_kline_fetch(max_retries=5, base_delay=5.0, max_delay=300.0)
    def fetch_kline(self, ticker: str, ...) -> KlineData:
        ...
    """
    return retry_with_backoff(
        max_retries=max_retries,
        base_delay=base_delay,
        max_delay=max_delay,
        jitter=jitter,
        retry_on=(Exception,),
    )
