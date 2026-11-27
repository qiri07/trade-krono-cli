"""trade_krono_cli.data_providers.factory_retry — 带限流和熔断的 Provider 重试逻辑。

职责（单一职责原则）：
  · 对单个 Provider 调用带指数退避重试，仅重试网络相关异常和 429
  · 硬故障立即放弃并降级，避免对明确不可恢复的错误做无谓重试
  · 统一记录限流成功 / 429 / 熔断计数，供 factory.py 调用
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from loguru import logger

from trade_krono_cli.data_providers.rate_limiter import RateLimiter

if TYPE_CHECKING:
    from collections.abc import Callable


def _is_429(error_msg: str) -> bool:
    """判断异常消息是否包含 429 限流标记。"""
    return "429" in error_msg or "rate limit" in error_msg.lower()


class FetchRetryPolicy:
    """单次 Provider 拉取的重试策略封装。

    只重试网络相关异常（ConnectionError/TimeoutError/OSError）以及包含
    ``429``/``rate limit`` 的异常；硬故障立即放弃并返回 ``None``。

    重试参数缺省从 Settings 读取（环境变量 RATE_LIMIT_*），也可通过
    显式传入覆盖，便于测试使用极短退避时间。
    """

    def __init__(
        self,
        max_retries: int | None = None,
        base_delay: float | None = None,
        max_delay: float | None = None,
    ) -> None:
        from trade_krono_cli.config import get_settings

        settings = get_settings()
        self.max_retries = (
            max_retries if max_retries is not None else settings.rate_limit_max_retries
        )
        self.base_delay = (
            base_delay if base_delay is not None else settings.rate_limit_base_backoff
        )
        self.max_delay = (
            max_delay if max_delay is not None else settings.rate_limit_max_backoff
        )
        self.retry_on = (ConnectionError, TimeoutError, OSError)

    def execute(
        self,
        name: str,
        ticker: str,
        fetch_fn: Callable[[], Any],
        op_label: str = "",
        rate_limiter: RateLimiter | None = None,
        record_fail: Callable[[str], None] | None = None,
        record_success: Callable[[str], None] | None = None,
        record_429: Callable[[str], None] | None = None,
    ) -> Any | None:
        """执行带重试的 Provider 调用。

        Args:
            name: Provider 名称（用于限流和熔断计数）。
            ticker: 股票代码。
            fetch_fn: 无参数可调用对象，执行实际的 Provider 拉取。
            op_label: 操作标签，用于日志输出（如 "行情"、"元数据"）。
            rate_limiter: 限流器实例，缺省使用全局单例。
            record_fail: 记录失败回调（委托给熔断器）。
            record_success: 记录成功回调（委托给熔断器）。
            record_429: 记录 429 回调。

        Returns:
            成功返回 fetch_fn 的结果，重试耗尽或硬故障返回 None。
        """
        limiter = rate_limiter or RateLimiter.get_instance()
        on_fail = record_fail or (lambda n: None)
        on_success = record_success or (lambda n: None)
        on_429 = record_429 or (lambda n: None)

        delay = self.base_delay
        last_error = ""

        for attempt in range(self.max_retries + 1):
            try:
                result = fetch_fn()
                if result is not None:
                    limiter.record_success(name)
                    on_success(name)
                    logger.debug(f"✅ {op_label}获取成功: {ticker} ← {name}")
                else:
                    logger.debug(f"{name} 返回空{op_label}: {ticker}")
                return result
            except self.retry_on as e:
                # 可重试异常：网络错误、超时等
                last_error = str(e)[:100]
                error_msg = str(e)
                if _is_429(error_msg):
                    limiter.record_429(name)
                    on_429(name)
                    delay = min(delay * 2, self.max_delay)

                if attempt == self.max_retries:
                    on_fail(name)
                    logger.warning(
                        f"{name} {op_label}拉取失败 {ticker}: {last_error} "
                        f"(重试{self.max_retries}次后放弃)"
                    )
                    return None

                wait = min(delay, self.max_delay)
                logger.warning(
                    f"⚠️  {name} 第{attempt + 1}次{op_label}失败: {last_error}, "
                    f"{wait:.1f}s后重试({attempt + 1}/{self.max_retries})"
                )
                time.sleep(wait)
                delay = min(delay * 2, self.max_delay)
            except Exception as e:
                # 不可重试异常（硬故障）：立即放弃
                last_error = str(e)[:100]
                error_msg = str(e)
                if _is_429(error_msg):
                    limiter.record_429(name)
                    on_429(name)
                on_fail(name)
                logger.warning(f"{name} {op_label}拉取非重试异常 {ticker}: {last_error}")
                return None

        return None


def fetch_kline_with_retry(
    provider: Any,
    name: str,
    ticker: str,
    start_date: str,
    end_date: str,
    frequency: str,
    adjustflag: str,
    policy: FetchRetryPolicy,
    rate_limiter: RateLimiter | None = None,
    record_fail: Callable[[str], None] | None = None,
    record_success: Callable[[str], None] | None = None,
    record_429: Callable[[str], None] | None = None,
) -> Any | None:
    """对单个 Provider 带指数退避重试地拉取 K 线。

    重试耗尽后返回 None，由调用方决定是否降级到下一个 Provider。
    空数据不重试，直接返回 None。

    Args:
        provider: Provider 实例。
        name: Provider 名称。
        ticker: 股票代码。
        start_date: 起始日期 YYYY-MM-DD。
        end_date: 结束日期 YYYY-MM-DD。
        frequency: K 线周期。
        adjustflag: 复权方式。
        policy: 重试策略实例。
        rate_limiter: 限流器实例。
        record_fail / record_success / record_429: 熔断器回调。

    Returns:
        成功返回 KlineData，重试耗尽或空数据返回 None。
    """
    limiter = rate_limiter or RateLimiter.get_instance()
    on_fail = record_fail or (lambda n: None)
    on_success = record_success or (lambda n: None)
    on_429 = record_429 or (lambda n: None)

    delay = policy.base_delay
    last_error = ""

    for attempt in range(policy.max_retries + 1):
        try:
            data = provider.fetch_kline(ticker, start_date, end_date, frequency, adjustflag)
            if data is not None and not data.is_empty:
                limiter.record_success(name)
                on_success(name)
                logger.info(
                    f"✅ K 线获取成功: {ticker} ← {name} ({len(data.timestamps)} 条)"
                )
                return data
            # 空数据不重试，直接返回 None
            logger.debug(f"{name} 返回空数据: {ticker}")
            return None
        except policy.retry_on as e:
            # 可重试异常：网络错误、超时等
            last_error = str(e)[:100]
            error_msg = str(e)
            if _is_429(error_msg):
                limiter.record_429(name)
                on_429(name)
                delay = min(delay * 2, policy.max_delay)

            if attempt == policy.max_retries:
                on_fail(name)
                logger.warning(
                    f"{name} K 线拉取失败 {ticker}: {last_error} "
                    f"(重试{policy.max_retries}次后放弃)"
                )
                return None

            wait = min(delay, policy.max_delay)
            logger.warning(
                f"⚠️  {name} 第{attempt + 1}次失败: {last_error}, "
                f"{wait:.1f}s后重试({attempt + 1}/{policy.max_retries})"
            )
            time.sleep(wait)
            delay = min(delay * 2, policy.max_delay)
        except Exception as e:
            # 不可重试异常（如 RuntimeError 等硬故障）：立即放弃，降级到下一个 provider
            last_error = str(e)[:100]
            error_msg = str(e)
            if _is_429(error_msg):
                limiter.record_429(name)
                on_429(name)
            on_fail(name)
            logger.warning(f"{name} K 线拉取非重试异常 {ticker}: {last_error}")
            return None

    return None
