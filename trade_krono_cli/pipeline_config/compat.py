"""PipelineConfig 向后兼容层 — 扁平属性代理与配置校验。"""

from __future__ import annotations

from typing import Any


def get_constraints(cfg: Any) -> Any:  # noqa: ANN401
    """向后兼容：constraints → trading。"""
    return cfg.trading


def get_delegated_attr(cfg: Any, name: str) -> Any:  # noqa: ANN401
    """向后兼容：将扁平字段访问委托给子配置。

    避免递归：只处理已知属性名。
    """
    _DELEGATES = {
        "sample_count": ("kronos", "sample_count"),
        "pred_len": ("kronos", "pred_len"),
        "lookback": ("kronos", "lookback"),
        "model_name": ("kronos", "model_name"),
        "device": ("kronos", "device"),
        "T": ("kronos", "T"),
        "top_p": ("kronos", "top_p"),
        "use_cache": ("kronos", "use_cache"),
        "llm_provider": ("ta", "llm_provider"),
        "deep_think_llm": ("ta", "deep_think_llm"),
        "quick_think_llm": ("ta", "quick_think_llm"),
        "max_debate_rounds": ("ta", "max_debate_rounds"),
        "output_language": ("ta", "output_language"),
        "min_confidence": ("filters", "min_confidence"),
        "allowed_signals": ("filters", "allowed_signals"),
        "market_cap_range": ("filters", "market_cap_range"),
        "industry_whitelist": ("filters", "industry_whitelist"),
        "industry_blacklist": ("filters", "industry_blacklist"),
        "pe_range": ("filters", "pe_range"),
        "pb_range": ("filters", "pb_range"),
        "max_risk_score": ("filters", "max_risk_score"),
        "min_volume_ratio": ("filters", "min_volume_ratio"),
        "min_turnover_rate": ("filters", "min_turnover_rate"),
        "exclude_st": ("filters", "exclude_st"),
        "skip_new_stock": ("abnormality", "skip_new_stock"),
        "new_stock_min_days": ("abnormality", "new_stock_min_days"),
        "kline_min_completeness": ("abnormality", "kline_min_completeness"),
        "abnormality_risk_boost_enabled": ("abnormality", "abnormality_risk_boost_enabled"),
        "output_dir": ("output", "output_dir"),
        "json_path": ("output", "json_path"),
        "html_path": ("output", "html_path"),
        "log_level": ("logging", "log_level"),
        "log_json": ("logging", "log_json"),
        "retry_max_attempts": ("retry", "retry_max_attempts"),
        "retry_base_delay": ("retry", "retry_base_delay"),
        "retry_jitter": ("retry", "retry_jitter"),
        "retry_rate_limit_backoff": ("retry", "retry_rate_limit_backoff"),
        "retry_rate_limit_max_wait": ("retry", "retry_rate_limit_max_wait"),
        "degrade_mode": ("degradation", "degrade_mode"),
        "ta_cache_fallback_enabled": ("degradation", "ta_cache_fallback_enabled"),
        "ta_cache_max_age_days": ("degradation", "ta_cache_max_age_days"),
        "universe_source": ("filters", "universe_source"),
    }
    if name in _DELEGATES:
        container, attr = _DELEGATES[name]
        return getattr(getattr(cfg, container), attr)
    msg = f"'{type(cfg).__name__}' object has no attribute '{name}'"
    raise AttributeError(msg)


def validate(cfg: Any) -> tuple[list[str], list[str]]:  # noqa: ANN401
    """校验所有子配置，返回 (errors, warnings)。"""
    errors: list[str] = []
    warnings: list[str] = []
    for name in (
        "kronos",
        "ta",
        "scoring",
        "risk",
        "filters",
        "abnormality",
        "trading",
        "retry",
        "degradation",
    ):
        sub = getattr(cfg, name)
        if hasattr(sub, "validate"):
            errs = sub.validate()
            errors.extend(errs)
    # 语义警告：ta_cache_fallback_enabled 与 degrade_mode 不匹配
    if (
        cfg.degradation.ta_cache_fallback_enabled
        and cfg.degradation.degrade_mode != "ta_cache_fallback"
    ):
        warnings.append(
            f"TA_CACHE_FALLBACK_ENABLED=true 但 DEGRADE_MODE="
            f"{cfg.degradation.degrade_mode}，"
            f"TA 缓存回退仅在 degrade_mode=ta_cache_fallback 时生效",
        )
    return errors, warnings
