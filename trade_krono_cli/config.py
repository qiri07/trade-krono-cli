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
    kronos_batch_size: int = field(
        default_factory=lambda: int(os.getenv("KRONOS_BATCH_SIZE", "8"))
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
    # ── 股票过滤配置 ────────────────────────────────────────
    filter_market_cap_range: str = field(
        default_factory=lambda: os.getenv("FILTER_MARKET_CAP_RANGE", "")
    )
    """市值范围（亿元），格式：\"50,5000\"，为空则不过滤。"""
    filter_industry_whitelist: str = field(
        default_factory=lambda: os.getenv("FILTER_INDUSTRY_WHITELIST", "")
    )
    """行业白名单，逗号分隔，如 \"银行,食品饮料\"。"""
    filter_industry_blacklist: str = field(
        default_factory=lambda: os.getenv("FILTER_INDUSTRY_BLACKLIST", "")
    )
    """行业黑名单，逗号分隔，如 \"房地产,煤炭\"。"""
    filter_pe_range: str = field(
        default_factory=lambda: os.getenv("FILTER_PE_RANGE", "")
    )
    """PE 区间，格式：\"5,30\"，为空则不过滤。"""
    filter_pb_range: str = field(
        default_factory=lambda: os.getenv("FILTER_PB_RANGE", "")
    )
    """PB 区间，格式：\"0,3\"，为空则不过滤。"""
    filter_max_risk_score: str = field(
        default_factory=lambda: os.getenv("FILTER_MAX_RISK_SCORE", "")
    )
    """风险分上限，0–1，为空则不过滤。"""
    filter_min_volume_ratio: str = field(
        default_factory=lambda: os.getenv("FILTER_MIN_VOLUME_RATIO", "")
    )
    """最小量比，为空则不过滤。"""
    filter_exclude_st: bool = field(
        default_factory=lambda: os.getenv("FILTER_EXCLUDE_ST", "true").lower() == "true"
    )

    filter_exclude_low_price: bool = field(
        default_factory=lambda: os.getenv("FILTER_EXCLUDE_LOW_PRICE", "true").lower() == "true"
    )
    """是否排除低价股（股价低于阈值）。"""
    filter_low_price_threshold: str = field(
        default_factory=lambda: os.getenv("FILTER_LOW_PRICE_THRESHOLD", "3.0")
    )
    """低价股阈值（元），低于此价被排除。"""
    filter_min_pb: str = field(
        default_factory=lambda: os.getenv("FILTER_MIN_PB", "")
    )
    """最低市净率，PB 低于此值视为高风险（空 = 不过滤）。"""

    # ── 异常股票处理配置 ──────────────────────────────────────
    filter_skip_suspended: bool = field(
        default_factory=lambda: os.getenv("FILTER_SKIP_SUSPENDED", "true").lower() == "true"
    )
    """是否跳过停牌股。"""
    filter_skip_new_stock: bool = field(
        default_factory=lambda: os.getenv("FILTER_SKIP_NEW_STOCK", "true").lower() == "true"
    )
    """是否跳过次新股（上市不足阈值天数）。"""
    filter_new_stock_min_days: int = field(
        default_factory=lambda: int(os.getenv("FILTER_NEW_STOCK_MIN_DAYS", "60"))
    )
    """次新股判定：上市不足此交易日数视为次新。"""
    filter_kline_min_completeness: float = field(
        default_factory=lambda: float(os.getenv("FILTER_KLINE_MIN_COMPLETENESS", "0.85"))
    )
    """K 线最低完整率阈值（0-1）。"""
    filter_abnormality_risk_boost_enabled: bool = field(
        default_factory=lambda: os.getenv("FILTER_ABNORMALITY_RISK_BOOST", "true").lower() == "true"
    )
    """是否根据异常标记上调风险分。"""

    # ── 评分策略配置 ──────────────────────────────────────
    scoring_strategy: str = field(
        default_factory=lambda: os.getenv("SCORING_STRATEGY", "linear")
    )
    """综合打分策略：linear / multiplicative / rank_based"""
    risk_boost_strategy: str = field(
        default_factory=lambda: os.getenv("RISK_BOOST_STRATEGY", "fixed_boost")
    )
    """异常标记风险加分策略：fixed_boost / scaled_boost / diminishing_boost"""
    risk_boost_multiplier: float = field(
        default_factory=lambda: float(os.getenv("RISK_BOOST_MULTIPLIER", "1.0"))
    )
    """risk_boost_strategy=scaled_boost 时的倍率"""
    risk_boost_diminishing_power: float = field(
        default_factory=lambda: float(os.getenv("RISK_BOOST_DIMINISHING_POWER", "0.5"))
    )
    """risk_boost_strategy=diminishing_boost 时的幂次（√n = power=0.5）"""

    # ── 重试策略配置 ────────────────────────────────────────
    retry_max_attempts: int = field(
        default_factory=lambda: int(os.getenv("RETRY_MAX_ATTEMPTS", "3"))
    )
    """最大重试次数（含首次）。"""
    retry_base_delay: float = field(
        default_factory=lambda: float(os.getenv("RETRY_BASE_DELAY", "2.0"))
    )
    """基础退避秒数。"""
    retry_jitter: bool = field(
        default_factory=lambda: os.getenv("RETRY_JITTER", "true").lower() == "true"
    )
    """是否添加随机抖动防止惊群。"""
    retry_rate_limit_backoff: bool = field(
        default_factory=lambda: os.getenv("RETRY_RATE_LIMIT_BACKOFF", "true").lower() == "true"
    )
    """限流时是否启用自适应退避（解析 Retry-After 头）。"""
    retry_rate_limit_max_wait: float = field(
        default_factory=lambda: float(os.getenv("RETRY_RATE_LIMIT_MAX_WAIT", "60.0"))
    )
    """限流自适应退避上限（秒）。"""

    # ── 降级策略配置 ────────────────────────────────────────
    degrade_mode: str = field(
        default_factory=lambda: os.getenv("DEGRADE_MODE", "strict")
    )
    """降级策略：strict | ta_only_on_kronos_fail | ta_cache_fallback"""
    ta_cache_fallback_enabled: bool = field(
        default_factory=lambda: os.getenv("TA_CACHE_FALLBACK_ENABLED", "false").lower() == "true"
    )
    """是否允许在 TA 失败时回退到最近一次缓存的 TA 结果（需显式开启）。"""
    ta_cache_max_age_days: int = field(
        default_factory=lambda: int(os.getenv("TA_CACHE_MAX_AGE_DAYS", "7"))
    )
    """TA 缓存结果最大有效期（天），超过则视为过期。"""

    # ── 数据源配置 ────────────────────────────────────────
    data_provider: str = field(
        default_factory=lambda: os.getenv("DATA_PROVIDER", "baostock")
    )
    """主数据源：baostock / akshare / mootdx / tushare"""
    data_fallback: str = field(
        default_factory=lambda: os.getenv("DATA_FALLBACK", "akshare,mootdx,tushare")
    )
    """备用数据源，逗号分隔，按优先级排列"""
    akshare_enabled: bool = field(
        default_factory=lambda: os.getenv("AKSHARE_ENABLED", "true").lower() == "true"
    )
    """是否启用 akshare 数据源"""
    mootdx_enabled: bool = field(
        default_factory=lambda: os.getenv("MOOTDX_ENABLED", "true").lower() == "true"
    )
    """是否启用 mootdx 数据源"""

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

    def available_data_sources(self) -> list[str]:
        """返回当前可用的数据源列表（按工厂链顺序过滤）。"""
        from trade_krono_cli.data_providers import get_data_factory
        factory = get_data_factory()
        return factory.available_providers()

    # ── 配置校验 ──────────────────────────────────────────
    def validate(self) -> tuple[list[str], list[str]]:
        """
        启动时统一校验所有必填配置、路径合法性、参数范围。

        Returns
        -------
        (errors, warnings)
          errors   — 致命问题，程序应终止
          warnings — 非致命问题，记录但不阻塞运行
        """
        from trade_krono_cli.config_validator import validate_settings
        return validate_settings(self)


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


def run_validation() -> tuple[list[str], list[str]]:
    """
    执行全量配置校验并返回 (errors, warnings)。
    此函数可在 CLI 入口调用，在日志初始化前暴露配置问题。
    """
    return get_settings().validate()
