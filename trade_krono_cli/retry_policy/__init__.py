"""retry_policy — 错误分级与差异化重试包。

向后兼容：保持原有 import 路径不变。
  from trade_krono_cli.retry_policy import (
      RetryPolicy, smart_retry, classify_error,
      NetworkError, TimeoutError, RateLimitError, Server5xxError,
      ParameterError, DataNotFoundError, AuthError, ValidationError,
      FailureStore, get_failure_store, clear_failure_store_singleton,
      parse_retry_after,
      make_rate_limit_error, make_network_error, make_timeout_error,
      make_data_not_found, make_auth_error, make_parameter_error,
  )
"""

from __future__ import annotations

# ── 工具函数（单例 + helpers）───────────────────────────────────────────────
from trade_krono_cli.retry_policy._legacy import (
    clear_failure_store_singleton,
    get_failure_store,
    make_auth_error,
    make_data_not_found,
    make_network_error,
    make_parameter_error,
    make_rate_limit_error,
    make_timeout_error,
    parse_retry_after,
)

# ── 分类器 ──────────────────────────────────────────────────────────────────
from trade_krono_cli.retry_policy.classifier import classify_error

# ── 异常类 ──────────────────────────────────────────────────────────────────
from trade_krono_cli.retry_policy.exceptions import (
    AuthError,
    DataNotFoundError,
    NetworkError,
    ParameterError,
    RateLimitError,
    Server5xxError,
    TimeoutError,
    TradeKronoNonRetryableError,
    TradeKronoRetryableError,
    ValidationError,
)

# ── 策略 + 装饰器 ──────────────────────────────────────────────────────────
from trade_krono_cli.retry_policy.policy import (  # noqa: F401
    RetryPolicy,
    _compute_delay,
    _exp_backoff,
    _make_smart_retry_decorator,
    smart_retry,
)

# ── 持久化 ──────────────────────────────────────────────────────────────────
from trade_krono_cli.retry_policy.store import (
    FailureRecord,
    FailureStore,
)

__all__ = [
    "AuthError",
    "DataNotFoundError",
    # 持久化
    "FailureRecord",
    "FailureStore",
    "NetworkError",
    "ParameterError",
    "RateLimitError",
    # 策略
    "RetryPolicy",
    "Server5xxError",
    "TimeoutError",
    "TradeKronoNonRetryableError",
    # 异常
    "TradeKronoRetryableError",
    "ValidationError",
    "classify_error",
    "clear_failure_store_singleton",
    "get_failure_store",
    "make_auth_error",
    "make_data_not_found",
    "make_network_error",
    "make_parameter_error",
    "make_rate_limit_error",
    "make_timeout_error",
    # 工具
    "parse_retry_after",
    "smart_retry",
]
