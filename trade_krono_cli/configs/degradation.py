"""降级策略配置。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=False)
class DegradationConfig:
    """优雅降级参数。"""

    degrade_mode: str = "strict"
    """降级策略：strict / ta_only_on_kronos_fail / ta_cache_fallback"""
    ta_cache_fallback_enabled: bool = False
    """是否允许在 TA 失败时回退到最近一次缓存的 TA 结果。"""
    ta_cache_max_age_days: int = 7
    """TA 缓存结果最大有效期（天）。"""

    VALID_MODES = {"strict", "ta_only_on_kronos_fail", "ta_cache_fallback"}

    def merge(self, **overrides) -> DegradationConfig:
        current = {k: getattr(self, k) for k in self.__dataclass_fields__}
        current.update(overrides)
        return DegradationConfig(**current)

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.degrade_mode not in self.VALID_MODES:
            errors.append(
                f"DEGRADE_MODE={self.degrade_mode} 必须是以下之一: "
                f"{', '.join(sorted(self.VALID_MODES))}",
            )
        if self.ta_cache_max_age_days < 1:
            errors.append("TA_CACHE_MAX_AGE_DAYS 必须 >= 1")
        elif self.ta_cache_max_age_days > 365:
            errors.append("TA_CACHE_MAX_AGE_DAYS 不应超过 365")
        if self.ta_cache_fallback_enabled and self.degrade_mode != "ta_cache_fallback":
            # Warning returned as empty list; caller handles it as a warning
            errors.append(
                f"TA_CACHE_FALLBACK_ENABLED=true 但 DEGRADE_MODE={self.degrade_mode}，"
                f"TA 缓存回退仅在 degrade_mode=ta_cache_fallback 时生效",
            )
        return errors
