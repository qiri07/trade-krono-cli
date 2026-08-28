"""重试策略配置。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=False)
class RetryConfig:
    """重试退避策略参数。"""

    retry_max_attempts: int = 3
    retry_base_delay: float = 2.0
    retry_jitter: bool = True
    retry_rate_limit_backoff: bool = True
    retry_rate_limit_max_wait: float = 60.0

    def merge(self, **overrides) -> "RetryConfig":
        current = {k: getattr(self, k) for k in self.__dataclass_fields__}
        current.update(overrides)
        return RetryConfig(**current)

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.retry_max_attempts < 1:
            errors.append("RETRY_MAX_ATTEMPTS 必须 >= 1")
        elif self.retry_max_attempts > 10:
            errors.append("RETRY_MAX_ATTEMPTS 不应超过 10")
        if self.retry_base_delay <= 0:
            errors.append("RETRY_BASE_DELAY 必须 > 0")
        elif self.retry_base_delay > 60:
            errors.append("RETRY_BASE_DELAY 不应超过 60s")
        if self.retry_rate_limit_max_wait <= 0:
            errors.append("RETRY_RATE_LIMIT_MAX_WAIT 必须 > 0")
        elif self.retry_rate_limit_max_wait > 300:
            errors.append("RETRY_RATE_LIMIT_MAX_WAIT 不应超过 300s")
        return errors
