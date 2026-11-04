"""LLM 端点 / Key / 模型的一致性解析。

背景：历史实现按固定优先级挑选 API Key（NVIDIA → OpenRouter → DeepSeek →
OpenAI → Agnes），却把 Key 发给由 ``NEMOTRON_BASE_URL`` / ``BACKEND_URL``
决定的端点。当 shell 环境残留一个过期的 ``OPENAI_API_KEY`` 时，它会被优先
选中并发往 Agnes/NVIDIA 端点，导致所有 AI 核实调用 401/404 失败。

本模块保证 Key 与端点所属 Provider 匹配：

1. 先由 ``NEMOTRON_BASE_URL`` / ``BACKEND_URL`` / ``LLM_BASE_URL`` 解析端点；
2. 再按端点域名匹配对应 Key（agnes → AGNES_API_KEY，nvidia → NVIDIA_API_KEY …）；
3. 域名无法识别时回退到 ``LLM_PROVIDER`` 映射；
4. 模型按端点族校验，避免把 agnes 模型发往 NVIDIA 端点（会返回 404）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
# 确保 .env 已加载（resolve_llm_config 可能在 config.py 的 load_dotenv 之前被调用）
load_dotenv(_PROJECT_ROOT / ".env", override=False)

DEFAULT_NEMOTRON_BASE_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_AGNES_BASE_URL = "https://apihub.agnes-ai.cn/v1"
DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"

# NVIDIA 官方端点要求模型 ID 带 `nvidia/` 前缀，缺前缀会返回 404。
DEFAULT_NVIDIA_MODEL = "nvidia/nemotron-3.5-super-vl-120b-a12b"
# OpenRouter 上的同名模型 ID 不带 120b-a12b 后缀。
DEFAULT_OPENROUTER_MODEL = "nvidia/nemotron-3.5-super-vl"
DEFAULT_NEMOTRON_MODEL = DEFAULT_NVIDIA_MODEL  # 向后兼容旧引用
DEFAULT_AGNES_MODEL = "agnes-3.0-flash"
DEFAULT_DEEPSEEK_MODEL = "deepseek-chat"

# 端点域名关键字 → 对应 Key 环境变量（顺序即匹配优先级）
_HOST_KEY_ENV: tuple[tuple[str, str], ...] = (
    ("nvidia", "NVIDIA_API_KEY"),
    ("openrouter", "OPENROUTER_API_KEY"),
    ("deepseek", "DEEPSEEK_API_KEY"),
    ("agnes", "AGNES_API_KEY"),
    ("openai.com", "OPENAI_API_KEY"),
)

# LLM_PROVIDER → 对应 Key 环境变量
_PROVIDER_KEY_ENV: dict[str, str] = {
    "nemotron": "NVIDIA_API_KEY",
    "nvidia": "NVIDIA_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openai_compatible": "OPENAI_COMPATIBLE_API_KEY",
    "agnes": "AGNES_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "minimax": "MINIMAX_API_KEY",
}

# 端点族 → 默认模型
_FAMILY_DEFAULT_MODEL: dict[str, str] = {
    "agnes": DEFAULT_AGNES_MODEL,
    "nvidia": DEFAULT_NVIDIA_MODEL,
    "openrouter": DEFAULT_OPENROUTER_MODEL,
    "deepseek": DEFAULT_DEEPSEEK_MODEL,
}

# 无匹配 Key 时的兜底顺序（保持历史行为，但仅作为最后手段）
_FALLBACK_KEY_ENVS: tuple[str, ...] = (
    "NVIDIA_API_KEY",
    "OPENROUTER_API_KEY",
    "DEEPSEEK_API_KEY",
    "OPENAI_API_KEY",
    "AGNES_API_KEY",
    "OPENAI_COMPATIBLE_API_KEY",
)


@dataclass(frozen=True)
class LlmConfig:
    """解析后的 LLM 连接配置。

    Attributes:
        api_key: 实际使用的 API Key；未配置任何 Key 时为 None。
        key_env: Key 来源的环境变量名（便于日志脱敏排查）。
        base_url: 端点地址（不含末尾斜杠）。
        model: 模型名（已按端点族校验）。
        provider: LLM_PROVIDER 的规范化小写值。
        family: 端点族（agnes / nvidia / openrouter / deepseek / unknown）。
    """

    api_key: str | None
    key_env: str | None
    base_url: str
    model: str
    provider: str
    family: str


def _env(name: str) -> str:
    """读取并清理环境变量（去空白）。"""
    return (os.getenv(name) or "").strip()


def _detect_family(base_url: str, provider: str) -> str:
    """根据端点域名（优先）与 provider 判断端点族。"""
    host = base_url.lower()
    for keyword in ("nvidia", "openrouter", "deepseek", "agnes"):
        if keyword in host:
            return keyword
    if provider in ("nemotron", "nvidia"):
        return "nvidia"
    if provider in _FAMILY_DEFAULT_MODEL:
        return provider
    return "unknown"


def _resolve_model(family: str, provider: str, model: str | None = None) -> str:
    """解析模型名，并校验其与端点族是否一致。

    Args:
        family: 端点族（agnes / nvidia / openrouter / deepseek / unknown）。
        provider: LLM_PROVIDER。
        model: 显式指定的模型名（AI_VERIFICATION_MODEL / NEMOTRON_MODEL）。

    Returns:
        最终使用的模型名；族不匹配时回落到该族默认模型。
    """
    default_model = _FAMILY_DEFAULT_MODEL.get(family) or DEFAULT_NEMOTRON_MODEL
    if not model:
        return default_model
    lowered = model.lower()
    if family == "agnes" and "agnes" not in lowered:
        return DEFAULT_AGNES_MODEL
    if family in ("nvidia", "openrouter") and "nemotron" not in lowered and "nvidia" not in lowered:
        return DEFAULT_NEMOTRON_MODEL
    if family == "deepseek" and "deepseek" not in lowered:
        return DEFAULT_DEEPSEEK_MODEL
    return model


def resolve_llm_config() -> LlmConfig:
    """解析一致的 (api_key, key_env, base_url, model) 组合。

    Returns:
        LlmConfig：Key 与端点强匹配的配置；无任何 Key 时 api_key 为 None。
    """
    provider = (_env("LLM_PROVIDER") or "nemotron").lower()

    base_url = _env("NEMOTRON_BASE_URL") or _env("BACKEND_URL") or _env("LLM_BASE_URL")
    if not base_url:
        if provider == "agnes":
            base_url = DEFAULT_AGNES_BASE_URL
        elif provider == "deepseek":
            base_url = DEFAULT_DEEPSEEK_BASE_URL
        else:
            base_url = DEFAULT_NEMOTRON_BASE_URL
    base_url = base_url.rstrip("/")

    family = _detect_family(base_url, provider)

    # 1) 端点域名 → 对应 Key（避免跨 Provider 混用 Key）
    key_env: str | None = None
    for keyword, env_name in _HOST_KEY_ENV:
        if keyword in base_url.lower():
            key_env = env_name
            break
    # 2) 域名无法识别 → 按 LLM_PROVIDER 映射
    if key_env is None:
        key_env = _PROVIDER_KEY_ENV.get(provider)

    api_key: str | None = None
    if key_env:
        api_key = _env(key_env) or None
    # 3) 最后手段：任一已配置 Key（仅在无法确定 Key 名称时）
    if api_key is None and key_env is None:
        for candidate in _FALLBACK_KEY_ENVS:
            value = _env(candidate)
            if value:
                api_key, key_env = value, candidate
                break

    explicit_model = (
        _env("AI_VERIFICATION_MODEL")
        or (_env("NEMOTRON_MODEL") if family in ("nvidia", "openrouter") else "")
    )
    model = _resolve_model(family, provider, explicit_model or None)

    return LlmConfig(
        api_key=api_key,
        key_env=key_env,
        base_url=base_url,
        model=model,
        provider=provider,
        family=family,
    )
