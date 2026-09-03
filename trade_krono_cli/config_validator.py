"""配置校验 — 在启动前验证 Settings 合法性，提前暴露问题而非运行时失败。

validate_settings() 返回错误列表；空列表表示配置合法。
可在 repo doctor / run 命令入口调用，提前终止非法配置。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from trade_krono_cli.config import Settings


def validate_settings(s: Settings) -> tuple[list[str], list[str]]:
    """校验配置合法性，返回 (errors, warnings) 元组。
    errors   — 致命问题，程序应终止
    warnings — 非致命问题，记录但不阻塞运行.
    """
    errors: list[str] = []
    warnings: list[str] = []

    # ── 整型 / 数值下限 ─────────────────────────────────────────────────────
    if s.kronos_lookback < 10:
        errors.append(f"kronos_lookback={s.kronos_lookback} 必须 >= 10")
    if s.kronos_pred_len < 1:
        errors.append(f"kronos_pred_len={s.kronos_pred_len} 必须 >= 1")
    if s.kronos_sample_count < 1:
        errors.append(f"kronos_sample_count={s.kronos_sample_count} 必须 >= 1")
    if s.kronos_batch_size < 1:
        errors.append(f"kronos_batch_size={s.kronos_batch_size} 必须 >= 1")
    if s.max_debate_rounds < 1:
        errors.append(f"max_debate_rounds={s.max_debate_rounds} 必须 >= 1")
    if s.max_risk_discuss_rounds < 1:
        errors.append(f"max_risk_discuss_rounds={s.max_risk_discuss_rounds} 必须 >= 1")

    # ── 正数约束 ────────────────────────────────────────────────────────────
    if s.baostock_sleep_sec <= 0:
        errors.append(f"baostock_sleep_sec={s.baostock_sleep_sec} 必须 > 0")
    if s.kronos_T <= 0:
        errors.append(f"kronos_T={s.kronos_T} 必须 > 0")

    # ── 范围约束 ────────────────────────────────────────────────────────────
    if not (0 <= s.default_min_confidence <= 100):
        errors.append(f"default_min_confidence={s.default_min_confidence} 必须在 [0, 100] 范围内")
    if not (0 < s.kronos_top_p <= 1.0):
        errors.append(f"kronos_top_p={s.kronos_top_p} 必须在 (0, 1.0] 范围内")

    # ── 非空字符串约束 ──────────────────────────────────────────────────────
    if not s.llm_provider or not s.llm_provider.strip():
        errors.append("llm_provider 不能为空")
    if not s.output_language or not s.output_language.strip():
        errors.append("output_language 不能为空")
    if not s.kronos_model or not s.kronos_model.strip():
        errors.append("kronos_model 不能为空")

    # ── 列表非空约束 ────────────────────────────────────────────────────────
    if not s.default_allowed_signals:
        errors.append("default_allowed_signals 不能为空")

    # ── 过滤配置校验 ────────────────────────────────────────────────────────
    # market_cap_range: "low,high"
    if s.filter_market_cap_range:
        parts = [p.strip() for p in s.filter_market_cap_range.split(",") if p.strip()]
        if len(parts) != 2:
            errors.append(
                f'FILTER_MARKET_CAP_RANGE={s.filter_market_cap_range} 格式应为 "low,high"',
            )
        else:
            try:
                lo, hi = float(parts[0]), float(parts[1])
                if lo >= hi:
                    errors.append(f"FILTER_MARKET_CAP_RANGE 下限({lo})必须小于上限({hi})")
            except ValueError:
                errors.append(f"FILTER_MARKET_CAP_RANGE={s.filter_market_cap_range} 包含非法数值")

    # pe_range / pb_range: same format
    for name, val in [
        ("FILTER_PE_RANGE", s.filter_pe_range),
        ("FILTER_PB_RANGE", s.filter_pb_range),
    ]:
        if val:
            parts = [p.strip() for p in val.split(",") if p.strip()]
            if len(parts) != 2:
                errors.append(f'{name}={val} 格式应为 "low,high"')
            else:
                try:
                    lo, hi = float(parts[0]), float(parts[1])
                    if lo >= hi:
                        errors.append(f"{name} 下限({lo})必须小于上限({hi})")
                except ValueError:
                    errors.append(f"{name}={val} 包含非法数值")

    # max_risk_score: 0–1
    if s.filter_max_risk_score:
        try:
            v = float(s.filter_max_risk_score)
            if not (0 <= v <= 1):
                errors.append(f"FILTER_MAX_RISK_SCORE={v} 必须在 [0, 1] 范围内")
        except ValueError:
            errors.append(f"FILTER_MAX_RISK_SCORE={s.filter_max_risk_score} 不是合法数值")

    # min_volume_ratio: > 0
    if s.filter_min_volume_ratio:
        try:
            v = float(s.filter_min_volume_ratio)
            if v <= 0:
                errors.append(f"FILTER_MIN_VOLUME_RATIO={v} 必须 > 0")
        except ValueError:
            errors.append(f"FILTER_MIN_VOLUME_RATIO={s.filter_min_volume_ratio} 不是合法数值")

    # ── 异常股票配置校验 ────────────────────────────────────────
    if s.filter_new_stock_min_days < 5:
        errors.append(f"FILTER_NEW_STOCK_MIN_DAYS={s.filter_new_stock_min_days} 必须 >= 5")

    kc = s.filter_kline_min_completeness
    try:
        if not (0 < kc <= 1.0):
            errors.append(f"FILTER_KLINE_MIN_COMPLETENESS={kc} 必须在 (0, 1.0] 范围内")
    except TypeError:
        errors.append(f"FILTER_KLINE_MIN_COMPLETENESS={kc} 不是合法数值")

    # ── 评分策略配置校验 ─────────────────────────────────────────────
    valid_scorers = {"linear", "multiplicative", "rank_based"}
    if s.scoring_strategy not in valid_scorers:
        errors.append(
            f"SCORING_STRATEGY={s.scoring_strategy} 必须是以下之一: "
            f"{', '.join(sorted(valid_scorers))}",
        )

    valid_boosters = {"fixed_boost", "scaled_boost", "diminishing_boost"}
    if s.risk_boost_strategy not in valid_boosters:
        errors.append(
            f"RISK_BOOST_STRATEGY={s.risk_boost_strategy} 必须是以下之一: "
            f"{', '.join(sorted(valid_boosters))}",
        )

    if not (0 < s.risk_boost_multiplier <= 5.0):
        errors.append(f"RISK_BOOST_MULTIPLIER={s.risk_boost_multiplier} 必须在 (0, 5.0] 范围内")

    if not (0 < s.risk_boost_diminishing_power <= 1.0):
        errors.append(
            f"RISK_BOOST_DIMINISHING_POWER={s.risk_boost_diminishing_power} 必须在 (0, 1.0] 范围内",
        )

    # ── 数据源配置校验 ─────────────────────────────────────────────────────
    valid_sources = {"baostock", "akshare", "mootdx", "tushare", "tonghuashun"}
    primary = s.data_provider.strip().lower() if s.data_provider else "baostock"
    if primary not in valid_sources:
        errors.append(
            f"DATA_PROVIDER={s.data_provider} 必须是以下之一: {', '.join(sorted(valid_sources))}",
        )

    if s.data_fallback:
        fallbacks = [f.strip() for f in s.data_fallback.split(",") if f.strip()]
        for fb in fallbacks:
            if fb.lower() not in valid_sources:
                errors.append(
                    f"DATA_FALLBACK 包含未知源: '{fb}'，合法值: {', '.join(sorted(valid_sources))}",
                )
        # 不能包含主源自身
        if primary in [f.lower() for f in fallbacks]:
            errors.append(f"DATA_FALLBACK 不能包含主数据源 '{primary}'")

    # ── 外部依赖目录（警告级，不影响核心流程）────────────────────────────────
    if s.tradingagents_root and not s.tradingagents_root.is_dir():
        warnings.append(f"TradingAgents 目录不存在: {s.tradingagents_root}")
    if s.kronos_root and not s.kronos_root.is_dir():
        warnings.append(f"Kronos 目录不存在: {s.kronos_root}")

    # ── LLM API Key 可用性（警告级）──────────────────────────────────────────
    if s.llm_provider:
        import os

        env_key = _PROVIDER_ENV_KEY.get(s.llm_provider.strip().lower())
        if env_key and not os.getenv(env_key):
            warnings.append(f"provider={s.llm_provider} 对应的环境变量 {env_key} 未设置")

    # ── 重试策略校验 ───────────────────────────────────────────────────────
    errors.extend(_validate_retry_policy(s))

    # ── 降级策略校验 ───────────────────────────────────────────────────────
    valid_degrade_modes = {"strict", "ta_only_on_kronos_fail", "ta_cache_fallback"}
    if s.degrade_mode not in valid_degrade_modes:
        errors.append(
            f"DEGRADE_MODE={s.degrade_mode} 必须是以下之一: "
            f"{', '.join(sorted(valid_degrade_modes))}",
        )
    if s.ta_cache_max_age_days < 1:
        errors.append("TA_CACHE_MAX_AGE_DAYS 必须 >= 1")
    elif s.ta_cache_max_age_days > 365:
        errors.append("TA_CACHE_MAX_AGE_DAYS 不应超过 365（避免缓存过期时间过长）")
    # 语义校验：ta_cache_fallback_enabled 仅在 ta_cache_fallback 模式下有意义
    if s.ta_cache_fallback_enabled and s.degrade_mode != "ta_cache_fallback":
        warnings.append(
            f"TA_CACHE_FALLBACK_ENABLED=true 但 DEGRADE_MODE={s.degrade_mode}，"
            f"TA 缓存回退仅在 degrade_mode=ta_cache_fallback 时生效",
        )

    # 由调用方格式化输出，此处仅作校验入口
    return errors, warnings


def _validate_retry_policy(s: Settings) -> list[str]:
    """校验重试策略参数。"""
    errs: list[str] = []
    if s.retry_max_attempts < 1:
        errs.append("RETRY_MAX_ATTEMPTS 必须 >= 1")
    elif s.retry_max_attempts > 10:
        errs.append("RETRY_MAX_ATTEMPTS 不应超过 10（避免无限重试）")
    if s.retry_base_delay <= 0:
        errs.append("RETRY_BASE_DELAY 必须 > 0")
    elif s.retry_base_delay > 60:
        errs.append("RETRY_BASE_DELAY 不应超过 60s")
    if s.retry_rate_limit_max_wait <= 0:
        errs.append("RETRY_RATE_LIMIT_MAX_WAIT 必须 > 0")
    elif s.retry_rate_limit_max_wait > 300:
        errs.append("RETRY_RATE_LIMIT_MAX_WAIT 不应超过 300s")
    return errs


# ── 内部：provider → 环境变量名映射（复用 security.py 的约定）────────────────
_PROVIDER_ENV_KEY = {
    "deepseek": "DEEPSEEK_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "minimax": "MINIMAX_API_KEY",
    "agnes": "AGNES_API_KEY",
}


def print_validation_report(errors: list[str], warnings: list[str]) -> bool:
    """打印校验报告到控制台，返回是否通过（无错误 = True）。

    Warnings（⚠️）不阻止运行，errors（❌）会。
    """
    if not errors and not warnings:
        return True

    if errors:
        logger.error("❌ 配置校验失败，请修复以下问题后再运行：")
        for e in errors:
            logger.error(f"  ❌ {e}")
    if warnings:
        logger.warning("⚠️  配置存在以下警告（不影响运行，建议修复）：")
        for w in warnings:
            logger.warning(f"  ⚠️  {w}")

    return not errors
