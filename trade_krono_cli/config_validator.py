"""
配置校验 — 在启动前验证 Settings 合法性，提前暴露问题而非运行时失败。

validate_settings() 返回错误列表；空列表表示配置合法。
可在 repo doctor / run 命令入口调用，提前终止非法配置。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from trade_krono_cli.config import Settings


def validate_settings(s: "Settings") -> tuple[List[str], List[str]]:
    """
    校验配置合法性，返回 (errors, warnings) 元组。
    errors   — 致命问题，程序应终止
    warnings — 非致命问题，记录但不阻塞运行
    """
    errors: List[str] = []
    warnings: List[str] = []

    # ── 整型 / 数值下限 ─────────────────────────────────────────────────────
    if s.kronos_lookback < 10:
        errors.append(f"kronos_lookback={s.kronos_lookback} 必须 >= 10")
    if s.kronos_pred_len < 1:
        errors.append(f"kronos_pred_len={s.kronos_pred_len} 必须 >= 1")
    if s.kronos_sample_count < 1:
        errors.append(f"kronos_sample_count={s.kronos_sample_count} 必须 >= 1")
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
        errors.append(
            f"default_min_confidence={s.default_min_confidence} 必须在 [0, 100] 范围内"
        )
    if not (0 < s.kronos_top_p <= 1.0):
        errors.append(
            f"kronos_top_p={s.kronos_top_p} 必须在 (0, 1.0] 范围内"
        )

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

    # ── 外部依赖目录（警告级，不影响核心流程）────────────────────────────────
    if s.tradingagents_root and not s.tradingagents_root.is_dir():
        warnings.append(
            f"TradingAgents 目录不存在: {s.tradingagents_root}"
        )
    if s.kronos_root and not s.kronos_root.is_dir():
        warnings.append(
            f"Kronos 目录不存在: {s.kronos_root}"
        )

    # ── LLM API Key 可用性（警告级）──────────────────────────────────────────
    if s.llm_provider:
        import os
        env_key = _PROVIDER_ENV_KEY.get(s.llm_provider.strip().lower())
        if env_key and not os.getenv(env_key):
            warnings.append(
                f"provider={s.llm_provider} 对应的环境变量 {env_key} 未设置"
            )

    all_messages = [f"❌ {e}" for e in errors] + [f"⚠️  {w}" for w in warnings]
    return errors, warnings


# ── 内部：provider → 环境变量名映射（复用 security.py 的约定）────────────────
_PROVIDER_ENV_KEY = {
    "deepseek": "DEEPSEEK_API_KEY",
    "openai":   "OPENAI_API_KEY",
    "anthropic":"ANTHROPIC_API_KEY",
    "minimax":  "MINIMAX_API_KEY",
    "agnes":    "AGNES_API_KEY",
}


def print_validation_report(
    errors: List[str], warnings: List[str]
) -> bool:
    """
    打印校验报告到控制台，返回是否通过（无错误 = True）。

    Warnings（⚠️）不阻止运行，errors（❌）会。
    """
    if not errors and not warnings:
        return True

    if errors:
        print("❌ 配置校验失败，请修复以下问题后再运行：")
        for e in errors:
            print(f"  ❌ {e}")
    if warnings:
        print("⚠️  配置存在以下警告（不影响运行，建议修复）：")
        for w in warnings:
            print(f"  ⚠️  {w}")

    return not errors
