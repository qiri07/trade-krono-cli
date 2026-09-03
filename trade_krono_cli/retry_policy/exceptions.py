"""retry_policy.exceptions — 错误分级定义。

异常层次：
  TradeKronoRetryableError    可重试错误（网络/限流/5xx）
  TradeKronoNonRetryableError 不可重试错误（参数/数据/鉴权）
"""

from __future__ import annotations


class TradeKronoRetryableError(Exception):
    """可重试错误的基类：网络超时、限流、服务端 5xx 等瞬时故障。"""


class TradeKronoNonRetryableError(Exception):
    """不可重试错误的基类：参数错误、数据不存在、鉴权失败等永久故障。"""


# ── 可重试子类 ────────────────────────────────────────────────────────────────


class NetworkError(TradeKronoRetryableError):
    """网络连接/超时错误。"""


class TimeoutError(TradeKronoRetryableError):
    """请求超时。"""


class RateLimitError(TradeKronoRetryableError):
    """限流错误（HTTP 429 / LLM rate limit）。

    Attributes
    ----------
    retry_after : float | None
        服务端建议的等待秒数（来自 Retry-After 头），None 表示未提供。

    """

    def __init__(
        self,
        message: str,
        retry_after: float | None = None,
        response_headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.retry_after = retry_after
        self.response_headers = response_headers or {}


class Server5xxError(TradeKronoRetryableError):
    """服务端 5xx 错误（非客户端责任）。"""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


# ── 不可重试子类 ──────────────────────────────────────────────────────────────


class ParameterError(TradeKronoNonRetryableError):
    """参数校验失败。"""


class DataNotFoundError(TradeKronoNonRetryableError):
    """数据不存在（如股票退市、停牌、K 线为空）。"""


class AuthError(TradeKronoNonRetryableError):
    """鉴权失败（密钥无效、token 过期）。"""


class ValidationError(TradeKronoNonRetryableError):
    """数据校验失败（格式异常、数据过旧）。"""
