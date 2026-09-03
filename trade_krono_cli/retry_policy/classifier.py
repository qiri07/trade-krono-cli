"""retry_policy.classifier — 错误分类器。

将异常分类为 (category, description)，
其中 category 为 "retriable" 或 "non_retriable"。
"""

from __future__ import annotations

import re

from trade_krono_cli.retry_policy.exceptions import (
    TradeKronoNonRetryableError,
    TradeKronoRetryableError,
)


def classify_error(exc: Exception) -> tuple[str, str]:
    """将异常分类为 (category, description)。

    Parameters
    ----------
    exc : Exception
        待分类的异常。

    Returns
    -------
    (category, description)
      category   : "retriable" | "non_retriable"
      description: 人类可读的错误描述

    """
    # 已经是明确的分类错误
    if isinstance(exc, TradeKronoRetryableError):
        return ("retriable", str(exc))
    if isinstance(exc, TradeKronoNonRetryableError):
        return ("non_retriable", str(exc))

    # 基于异常类型推断
    exc_type = type(exc).__name__
    msg = str(exc).lower()

    # 参数错误
    if any(kw in msg for kw in ("invalid", "illegal", "bad parameter", "missing required")):
        return ("non_retriable", f"参数错误: {exc}")

    # 数据不存在
    if any(
        kw in msg
        for kw in (
            "not found",
            "no data",
            "empty data",
            "data not found",
            "数据不足",
            "空数据",
            "数据不存在",
            "数据为空",
        )
    ):
        return ("non_retriable", f"数据缺失: {exc}")

    # 鉴权失败
    if any(
        kw in msg
        for kw in (
            "auth",
            "unauthorized",
            "forbidden",
            "invalid api key",
            "鉴权",
            "认证失败",
            "权限不足",
            "密钥无效",
            "401",
            "403",
        )
    ):
        return ("non_retriable", f"鉴权失败: {exc}")

    # 限流
    if any(kw in msg for kw in ("rate limit", "too many request", "429", "限流", "请求过于频繁")):
        return ("retriable", f"限流: {exc}")

    # 服务端 5xx
    if re.search(r"\b5\d\d\b", msg) or any(kw in msg for kw in ("500", "502", "503", "504")):
        return ("retriable", f"服务端错误: {exc}")

    # 网络/超时
    if exc_type in (
        "ConnectionError",
        "TimeoutError",
        "ConnectTimeout",
        "ReadTimeout",
        "URLError",
        "OSError",
    ) or any(kw in msg for kw in ("connection", "timeout", "network", "网络", "超时")):
        return ("retriable", f"网络错误: {exc}")

    # 数据验证失败
    if any(
        kw in msg
        for kw in ("invalid date", "data validation", "format", "数据格式", "数据过旧", "未来数据")
    ):
        return ("non_retriable", f"数据验证失败: {exc}")

    # 默认：未知错误，保守处理为不可重试（避免无限重试消耗资源）
    return ("non_retriable", f"未知错误: {exc}")
