"""utils.st_cache — ST 股票状态缓存工具。

提供带 TTL 的模块级缓存，用于 ST/停牌检测等高频查询场景。

示例：
    @cached(ttl=1800)
    def check_st(ticker: str) -> bool:
        ...
"""

from __future__ import annotations

import time
from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


def cached(ttl: float = 1800) -> Callable[[F], F]:
    """装饰器：为函数结果添加 TTL 缓存。

    Parameters
    ----------
    ttl : float
        缓存存活时间（秒），默认 1800（30 分钟）

    """

    def decorator(func: F) -> F:
        cache: dict[tuple, tuple[Any, float]] = {}

        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
            try:
                key = (args, tuple(sorted(kwargs.items())))
            except TypeError:
                # 参数含不可哈希类型（如 dataclass），跳过缓存直接调用
                return func(*args, **kwargs)
            now = time.time()
            try:
                if key in cache:
                    value, ts = cache[key]
                    if now - ts < ttl:
                        return value
                    del cache[key]
            except TypeError:
                # 哈希冲突时回退到直接调用
                return func(*args, **kwargs)
            result = func(*args, **kwargs)
            try:
                cache[key] = (result, now)
            except TypeError:
                pass  # 结果不可哈希，放弃缓存
            return result

        def clear() -> None:
            cache.clear()

        wrapper.clear = clear  # type: ignore[attr-defined]
        return wrapper  # type: ignore[return-value]

    return decorator
