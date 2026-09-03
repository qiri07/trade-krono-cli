"""retry_policy._legacy — 模块级单例和工厂函数（原 retry_policy.py 底部）。

不暴露为公共 API，仅供 __init__.py 内部使用。
"""

from __future__ import annotations

from trade_krono_cli.retry_policy.exceptions import (
    AuthError,
    DataNotFoundError,
    NetworkError,
    ParameterError,
    RateLimitError,
    TimeoutError,
)
from trade_krono_cli.retry_policy.store import FailureStore

_failure_store: FailureStore | None = None


def get_failure_store() -> FailureStore:
    global _failure_store
    if _failure_store is None:
        _failure_store = FailureStore()
    return _failure_store


def clear_failure_store_singleton() -> None:
    """清除单例，用于测试隔离。"""
    global _failure_store
    _failure_store = None


def parse_retry_after(header_value: str | None) -> float | None:
    """解析 HTTP Retry-After 响应头。

    支持两种格式：
      - 整数秒数："120"
      - HTTP 日期字符串："Wed, 21 Oct 2025 07:28:00 GMT"

    Returns
    -------
    float | None
        等待秒数，无法解析时返回 None。

    """
    import time

    if not header_value:
        return None
    header_value = header_value.strip()

    # 格式 1：秒数
    try:
        seconds = float(header_value)
        if seconds >= 0:
            return seconds
    except ValueError:
        pass

    # 格式 2：HTTP 日期
    try:
        from email.utils import parsedate_to_datetime

        dt = parsedate_to_datetime(header_value)
        now = time.time()
        wait = dt.timestamp() - now
        return max(wait, 0.0)
    except (ValueError, OSError):
        pass

    return None


def make_rate_limit_error(
    message: str,
    headers: dict[str, str] | None = None,
) -> RateLimitError:
    """从 HTTP 响应构建 RateLimitError，自动提取 Retry-After 头。"""
    retry_after = None
    if headers:
        for key in ("Retry-After", "retry-after", "X-RateLimit-Reset"):
            val = headers.get(key)
            if val:
                retry_after = parse_retry_after(val)
                if retry_after is not None:
                    break
    return RateLimitError(message=message, retry_after=retry_after, response_headers=headers or {})


def make_network_error(message: str) -> NetworkError:
    return NetworkError(message)


def make_timeout_error(message: str) -> TimeoutError:
    return TimeoutError(message)


def make_data_not_found(message: str) -> DataNotFoundError:
    return DataNotFoundError(message)


def make_auth_error(message: str) -> AuthError:
    return AuthError(message)


def make_parameter_error(message: str) -> ParameterError:
    return ParameterError(message)
