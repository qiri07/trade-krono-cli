"""
配置管理 — 从 .env 和环境变量加载，提供默认值。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# 加载项目根目录的 .env
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env", override=False)


@dataclass(frozen=True)
class Settings:
    """全局配置单例。"""

    # ── 路径配置（可从 .env 覆盖）────────────────────────────────
    tradingagents_root: Path = field(
        default_factory=lambda: _PROJECT_ROOT / "external" / "TradingAgents-astock"
    )
    kronos_root: Path = field(
        default_factory=lambda: _PROJECT_ROOT / "external" / "Kronos"
    )
    project_root: Path = field(
        default_factory=lambda: _PROJECT_ROOT
    )
    results_dir: Path = field(
        default_factory=lambda: _PROJECT_ROOT / "outputs" / "results"
    )
    cache_dir: Path = field(
        default_factory=lambda: _PROJECT_ROOT / "outputs" / "cache"
    )
    memory_log_path: Path = field(
        default_factory=lambda: _PROJECT_ROOT / "outputs" / "memory_log.jsonl"
    )

    # ── LLM 配置 ──────────────────────────────────────────
    llm_provider: str = field(
        default_factory=lambda: os.getenv("LLM_PROVIDER", "deepseek")
    )
    deep_think_llm: str = field(
        default_factory=lambda: os.getenv("DEEP_THINK_LLM", "deepseek-chat")
    )
    quick_think_llm: str = field(
        default_factory=lambda: os.getenv("QUICK_THINK_LLM", "deepseek-chat")
    )
    backend_url: Optional[str] = field(
        default_factory=lambda: os.getenv("BACKEND_URL", None)
    )
    max_debate_rounds: int = field(
        default_factory=lambda: int(os.getenv("MAX_DEBATE_ROUNDS", "1"))
    )
    max_risk_discuss_rounds: int = field(
        default_factory=lambda: int(os.getenv("MAX_RISK_DISCUSS_ROUNDS", "1"))
    )
    checkpoint_enabled: bool = field(
        default_factory=lambda: os.getenv("CHECKPOINT_ENABLED", "true").lower() == "true"
    )
    output_language: str = field(
        default_factory=lambda: os.getenv("OUTPUT_LANGUAGE", "Chinese")
    )

    # ── Kronos 配置 ───────────────────────────────────────
    kronos_model: str = field(
        default_factory=lambda: os.getenv("KRONOS_MODEL", "kronos-base")
    )
    kronos_tokenizer: str = field(
        default_factory=lambda: os.getenv("KRONOS_TOKENIZER", "kronos-Tokenizer-base")
    )
    kronos_device: str = field(
        default_factory=lambda: os.getenv("KRONOS_DEVICE", "cpu")
    )
    kronos_lookback: int = field(
        default_factory=lambda: int(os.getenv("KRONOS_LOOKBACK", "400"))
    )
    kronos_pred_len: int = field(
        default_factory=lambda: int(os.getenv("KRONOS_PRED_LEN", "30"))
    )
    kronos_sample_count: int = field(
        default_factory=lambda: int(os.getenv("KRONOS_SAMPLE_COUNT", "5"))
    )
    kronos_T: float = field(
        default_factory=lambda: float(os.getenv("KRONOS_T", "1.0"))
    )
    kronos_top_p: float = field(
        default_factory=lambda: float(os.getenv("KRONOS_TOP_P", "0.9"))
    )
    kronos_use_sample_confidence: bool = field(
        default_factory=lambda: os.getenv("KRONOS_USE_SAMPLE_CONFIDENCE", "false").lower() == "true"
    )

    # ── 过滤配置 ──────────────────────────────────────────
    default_min_confidence: float = field(
        default_factory=lambda: float(os.getenv("MIN_CONFIDENCE", "55.0"))
    )
    default_allowed_signals: list[str] = field(
        default_factory=lambda: [
            s.strip().upper()
            for s in os.getenv("ALLOWED_SIGNALS", "BUY,HOLD").split(",")
            if s.strip()
        ]
    )

    # ── 数据获取配置 ──────────────────────────────────────
    baostock_sleep_sec: float = field(
        default_factory=lambda: float(os.getenv("BAOSTOCK_SLEEP_SEC", "1.0"))
    )

    # ── 运行时路径 ────────────────────────────────────────
    def __post_init__(self):
        """确保目录存在。"""
        for p in (self.results_dir, self.cache_dir):
            p.mkdir(parents=True, exist_ok=True)

    # ── 密钥检查 ──────────────────────────────────────────
    def available_providers(self) -> list[str]:
        """返回已配置 API key 的 LLM 供应商列表。"""
        available = []
        for key in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "MINIMAX_API_KEY", "AGNES_API_KEY"):
            if os.getenv(key):
                available.append(key.replace("_API_KEY", "").lower())
        return available


# 模块级单例
_settings: Optional[Settings] = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reload_settings() -> Settings:
    """重新加载配置（用于测试）。"""
    global _settings
    _settings = Settings()
    return _settings


def clear_settings() -> None:
    """清除全局单例，使下一次 get_settings() 重新初始化。用于测试隔离。"""
    global _settings
    _settings = None
