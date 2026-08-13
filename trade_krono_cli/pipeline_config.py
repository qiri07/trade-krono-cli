"""
PipelineConfig — 流水线配置（复合对象）。

所有业务子配置集中在 trade_krono_cli.configs.* 模块中：

  configs/kronos.py       → KronosConfig      （Kronos 预测参数）
  configs/ta.py           → TAConfig          （TradingAgents 分析参数）
  configs/scoring.py      → ScoringConfig     （综合打分权重与阈值）
  configs/scoring.py      → ScoringStrategyConfig / RiskBoostStrategyConfig
  configs/risk.py         → RiskConfig        （风险引擎参数）
  configs/filters.py      → FilterConfig      （股票过滤参数）
  configs/abnormality.py  → AbnormalityConfig （异常股票处理参数）
  configs/trading.py      → ConstraintConfig  （A 股交易约束参数）
  configs/output.py       → OutputConfig      （输出路径）
  configs/logging.py      → LoggingConfig     （日志级别）
  configs/retry.py        → RetryConfig       （重试退避策略）
  configs/degradation.py  → DegradationConfig （优雅降级策略）

PipelineConfig 是顶层容器，通过属性代理保持向后兼容：
  cfg.sample_count        # → cfg.kronos.sample_count
  cfg.min_confidence      # → cfg.filters.min_confidence
  cfg.degrade_mode        # → cfg.degradation.degrade_mode

参数优先级（高 → 低）：
  1. CLI 命令行参数（typer.Option）
  2. 环境变量 / .env
  3. PipelineConfig（YAML/JSON 文件）
  4. 各子配置默认值
"""
from __future__ import annotations

import json
from dataclasses import asdict, field
from pathlib import Path
from typing import Any, Optional

from trade_krono_cli.config import get_settings, Settings
from trade_krono_cli.configs.kronos import KronosConfig
from trade_krono_cli.configs.ta import TAConfig
from trade_krono_cli.configs.scoring import (
    ScoringConfig,
    ScoringStrategyConfig,
    RiskBoostStrategyConfig,
)
from trade_krono_cli.configs.risk import RiskConfig
from trade_krono_cli.configs.filters import FilterConfig
from trade_krono_cli.configs.abnormality import AbnormalityConfig
from trade_krono_cli.configs.trading import ConstraintConfig
from trade_krono_cli.configs.output import OutputConfig
from trade_krono_cli.configs.logging import LoggingConfig
from trade_krono_cli.configs.retry import RetryConfig
from trade_krono_cli.configs.degradation import DegradationConfig


# ── 配置解析辅助函数 ─────────────────────────────────────────────────────────

def _parse_range(s: str) -> tuple[float, float] | None:
    if not s or not s.strip():
        return None
    parts = [p.strip() for p in s.split(",") if p.strip()]
    if len(parts) != 2:
        return None
    try:
        return float(parts[0]), float(parts[1])
    except ValueError:
        return None


def _parse_comma_list(s: str) -> list[str]:
    if not s or not s.strip():
        return []
    return [p.strip() for p in s.split(",") if p.strip()]


def _parse_float(s: str) -> float | None:
    if not s or not s.strip():
        return None
    try:
        return float(s.strip())
    except ValueError:
        return None


# ── PipelineConfig ────────────────────────────────────────────────────────────

class PipelineConfig:
    """
    流水线配置（复合对象）。

    用法：
        cfg = PipelineConfig.default()
        cfg = PipelineConfig.load("config.yaml")
        cfg = PipelineConfig.default().override(sample_count=10)
        cfg = PipelineConfig.default().override(kronos={"sample_count": 10})
    """

    def __init__(
        self,
        *,
        kronos: Optional[KronosConfig] = None,
        ta: Optional[TAConfig] = None,
        scoring: Optional[ScoringConfig] = None,
        scoring_strategy: Optional[ScoringStrategyConfig] = None,
        risk_boost_strategy: Optional[RiskBoostStrategyConfig] = None,
        risk: Optional[RiskConfig] = None,
        filters: Optional[FilterConfig] = None,
        abnormality: Optional[AbnormalityConfig] = None,
        trading: Optional[ConstraintConfig] = None,
        output: Optional[OutputConfig] = None,
        logging: Optional[LoggingConfig] = None,
        retry: Optional[RetryConfig] = None,
        degradation: Optional[DegradationConfig] = None,
        # 向后兼容：扁平字段（优先级高于子配置默认值）
        sample_count: Optional[int] = None,
        pred_len: Optional[int] = None,
        lookback: Optional[int] = None,
        model_name: Optional[str] = None,
        device: Optional[str] = None,
        T: Optional[float] = None,
        top_p: Optional[float] = None,
        use_cache: Optional[bool] = None,
        llm_provider: Optional[str] = None,
        deep_think_llm: Optional[str] = None,
        quick_think_llm: Optional[str] = None,
        max_debate_rounds: Optional[int] = None,
        output_language: Optional[str] = None,
        min_confidence: Optional[float] = None,
        allowed_signals: Optional[tuple[str, ...]] = None,
        market_cap_range: Optional[tuple[float, float]] = None,
        industry_whitelist: Optional[list[str]] = None,
        industry_blacklist: Optional[list[str]] = None,
        pe_range: Optional[tuple[float, float]] = None,
        pb_range: Optional[tuple[float, float]] = None,
        max_risk_score: Optional[float] = None,
        min_volume_ratio: Optional[float] = None,
        min_turnover_rate: Optional[float] = None,
        exclude_st: Optional[bool] = None,
        skip_new_stock: Optional[bool] = None,
        new_stock_min_days: Optional[int] = None,
        kline_min_completeness: Optional[float] = None,
        abnormality_risk_boost_enabled: Optional[bool] = None,
        output_dir: Optional[Path] = None,
        json_path: Optional[str] = None,
        html_path: Optional[str] = None,
        log_level: Optional[str] = None,
        log_json: Optional[bool] = None,
        retry_max_attempts: Optional[int] = None,
        retry_base_delay: Optional[float] = None,
        retry_jitter: Optional[bool] = None,
        retry_rate_limit_backoff: Optional[bool] = None,
        retry_rate_limit_max_wait: Optional[float] = None,
        degrade_mode: Optional[str] = None,
        ta_cache_fallback_enabled: Optional[bool] = None,
        ta_cache_max_age_days: Optional[int] = None,
        universe_source: Optional[str] = None,
    ):
        # 构建各子配置（优先用显式参数，其次用扁平字段覆盖，最后用默认值）
        self.kronos = self._merge_sub(
            KronosConfig(),
            kronos,
            {k: v for k, v in [
                ("sample_count", sample_count), ("pred_len", pred_len),
                ("lookback", lookback), ("model_name", model_name),
                ("device", device), ("T", T), ("top_p", top_p),
                ("use_cache", use_cache),
            ] if v is not None},
        )
        self.ta = self._merge_sub(
            TAConfig(),
            ta,
            {k: v for k, v in [
                ("llm_provider", llm_provider),
                ("deep_think_llm", deep_think_llm),
                ("quick_think_llm", quick_think_llm),
                ("max_debate_rounds", max_debate_rounds),
                ("output_language", output_language),
            ] if v is not None},
        )
        self.scoring = self._merge_sub(ScoringConfig(), scoring, {})
        self.scoring_strategy = self._merge_sub(ScoringStrategyConfig(), scoring_strategy, {})
        self.risk_boost_strategy = self._merge_sub(RiskBoostStrategyConfig(), risk_boost_strategy, {})
        self.risk = self._merge_sub(RiskConfig(), risk, {})
        self.filters = self._merge_sub(
            FilterConfig(),
            filters,
            {k: v for k, v in [
                ("min_confidence", min_confidence),
                ("allowed_signals", allowed_signals),
                ("market_cap_range", market_cap_range),
                ("industry_whitelist", industry_whitelist),
                ("industry_blacklist", industry_blacklist),
                ("pe_range", pe_range),
                ("pb_range", pb_range),
                ("max_risk_score", max_risk_score),
                ("min_volume_ratio", min_volume_ratio),
                ("min_turnover_rate", min_turnover_rate),
                ("exclude_st", exclude_st),
                ("universe_source", universe_source),
            ] if v is not None},
        )
        self.abnormality = self._merge_sub(
            AbnormalityConfig(),
            abnormality,
            {k: v for k, v in [
                ("skip_new_stock", skip_new_stock),
                ("new_stock_min_days", new_stock_min_days),
                ("kline_min_completeness", kline_min_completeness),
                ("abnormality_risk_boost_enabled", abnormality_risk_boost_enabled),
            ] if v is not None},
        )
        self.trading = self._merge_sub(
            ConstraintConfig(),
            trading,
            {},  # trading has no flat override fields
        )
        self.output = self._merge_sub(
            OutputConfig(),
            output,
            {k: v for k, v in [
                ("output_dir", output_dir),
                ("json_path", json_path),
                ("html_path", html_path),
            ] if v is not None},
        )
        self.logging = self._merge_sub(
            LoggingConfig(),
            logging,
            {k: v for k, v in [
                ("log_level", log_level),
                ("log_json", log_json),
            ] if v is not None},
        )
        self.retry = self._merge_sub(
            RetryConfig(),
            retry,
            {k: v for k, v in [
                ("retry_max_attempts", retry_max_attempts),
                ("retry_base_delay", retry_base_delay),
                ("retry_jitter", retry_jitter),
                ("retry_rate_limit_backoff", retry_rate_limit_backoff),
                ("retry_rate_limit_max_wait", retry_rate_limit_max_wait),
            ] if v is not None},
        )
        self.degradation = self._merge_sub(
            DegradationConfig(),
            degradation,
            {k: v for k, v in [
                ("degrade_mode", degrade_mode),
                ("ta_cache_fallback_enabled", ta_cache_fallback_enabled),
                ("ta_cache_max_age_days", ta_cache_max_age_days),
            ] if v is not None},
        )

    @staticmethod
    def _merge_sub(default, explicit, flat_overrides: dict) -> Any:
        """合并子配置：flat_overrides > explicit > default。

        注意字典展开顺序：后者覆盖前者。
        """
        if explicit is not None:
            if isinstance(explicit, dict):
                merged = {**default.__dict__, **explicit}
            else:
                merged = {**default.__dict__, **explicit.__dict__}
            # flat_overrides 优先级最高，覆盖 explicit 的值
            merged.update(flat_overrides)
            return type(default)(**merged)
        if flat_overrides:
            merged = {**default.__dict__, **flat_overrides}
            return type(default)(**merged)
        return default

    @classmethod
    def default(cls, settings: Optional[Settings] = None) -> "PipelineConfig":
        s = settings or get_settings()
        return cls(
            kronos=KronosConfig(
                sample_count=s.kronos_sample_count,
                pred_len=s.kronos_pred_len,
                lookback=s.kronos_lookback,
                model_name=s.kronos_model,
                device=s.kronos_device,
                T=s.kronos_T,
                top_p=s.kronos_top_p,
                use_cache=not s.kronos_sample_count == 0,
            ),
            ta=TAConfig(
                llm_provider=s.llm_provider,
                deep_think_llm=s.deep_think_llm,
                quick_think_llm=s.quick_think_llm,
                max_debate_rounds=s.max_debate_rounds,
                output_language=s.output_language,
            ),
            scoring=ScoringConfig(),
            scoring_strategy=ScoringStrategyConfig(strategy=s.scoring_strategy),
            risk_boost_strategy=RiskBoostStrategyConfig(
                strategy=s.risk_boost_strategy,
                multiplier=s.risk_boost_multiplier,
                diminishing_power=s.risk_boost_diminishing_power,
            ),
            risk=RiskConfig(),
            filters=FilterConfig(
                min_confidence=s.default_min_confidence,
                allowed_signals=tuple(s.default_allowed_signals),
                market_cap_range=_parse_range(s.filter_market_cap_range),
                industry_whitelist=_parse_comma_list(s.filter_industry_whitelist),
                industry_blacklist=_parse_comma_list(s.filter_industry_blacklist),
                pe_range=_parse_range(s.filter_pe_range),
                pb_range=_parse_range(s.filter_pb_range),
                max_risk_score=_parse_float(s.filter_max_risk_score),
                min_volume_ratio=_parse_float(s.filter_min_volume_ratio),
                exclude_st=s.filter_exclude_st,
            ),
            abnormality=AbnormalityConfig(
                skip_new_stock=s.filter_skip_new_stock,
                new_stock_min_days=s.filter_new_stock_min_days,
                kline_min_completeness=s.filter_kline_min_completeness,
                abnormality_risk_boost_enabled=s.filter_abnormality_risk_boost_enabled,
            ),
            retry=RetryConfig(
                retry_max_attempts=s.retry_max_attempts,
                retry_base_delay=s.retry_base_delay,
                retry_jitter=s.retry_jitter,
                retry_rate_limit_backoff=s.retry_rate_limit_backoff,
                retry_rate_limit_max_wait=s.retry_rate_limit_max_wait,
            ),
            degradation=DegradationConfig(
                degrade_mode=s.degrade_mode,
                ta_cache_fallback_enabled=s.ta_cache_fallback_enabled,
                ta_cache_max_age_days=s.ta_cache_max_age_days,
            ),
            output=OutputConfig(output_dir=s.results_dir.parent),
        )

    def override(self, **kwargs) -> "PipelineConfig":
        """
        返回新 PipelineConfig，支持扁平和嵌套覆盖。

        扁平覆盖（向后兼容）：
            override(sample_count=10, min_confidence=40.0)

        嵌套覆盖：
            override(kronos={"sample_count": 10})
            override(risk={"weights__volatility": 0.35})
        """
        # 分离扁平字段和嵌套子配置
        flat_keys = {
            "sample_count", "pred_len", "lookback", "model_name", "device",
            "T", "top_p", "use_cache",
            "llm_provider", "deep_think_llm", "quick_think_llm",
            "max_debate_rounds", "output_language",
            "min_confidence", "allowed_signals",
            "market_cap_range", "industry_whitelist", "industry_blacklist",
            "pe_range", "pb_range", "max_risk_score",
            "min_volume_ratio", "min_turnover_rate", "exclude_st",
            "skip_new_stock", "new_stock_min_days",
            "kline_min_completeness", "abnormality_risk_boost_enabled",
            "output_dir", "json_path", "html_path",
            "log_level", "log_json",
            "retry_max_attempts", "retry_base_delay", "retry_jitter",
            "retry_rate_limit_backoff", "retry_rate_limit_max_wait",
            "degrade_mode", "ta_cache_fallback_enabled", "ta_cache_max_age_days",
            "universe_source",
            # 向后兼容别名
            "constraints",
        }
        sub_config_keys = {
            "kronos", "ta", "scoring", "risk", "filters", "abnormality",
            "trading", "output", "logging", "retry", "degradation",
            "scoring_strategy", "risk_boost_strategy",
        }
        # 向后兼容别名：key → 实际子配置名
        _sub_config_aliases: dict[str, str] = {"constraints": "trading"}

        flat_overrides = {}
        sub_overrides: dict[str, Any] = {}

        for k, v in kwargs.items():
            # 处理向后兼容别名（constraints → trading）
            if k == "constraints" and isinstance(v, dict):
                sub_overrides["trading"] = v
            elif k in flat_keys:
                flat_overrides[k] = v
            elif k in sub_config_keys:
                sub_overrides[k] = v
            # 忽略未知 key

        # 对子配置，将当前值与覆盖值合并
        merged_sub: dict[str, Any] = {}
        for name, override in sub_overrides.items():
            current = getattr(self, name)
            if isinstance(override, dict):
                # 嵌套 dict 覆盖：处理 "__" 嵌套路径
                merged_sub[name] = self._merge_with_nested(current, override)
            else:
                merged_sub[name] = override
        # 处理 constraints → trading 别名
        if "constraints" in sub_overrides and "trading" not in merged_sub:
            merged_sub["trading"] = sub_overrides["constraints"]

        # 构建扁平覆盖（只含非 None 值）
        flat_filtered = {k: v for k, v in flat_overrides.items() if v is not None}

        # 构建新实例：先传入所有子配置，再传入扁平覆盖
        kwargs_new: dict[str, Any] = {name: merged_sub.get(name, getattr(self, name))
                                       for name in sub_config_keys}
        kwargs_new.update(flat_filtered)
        return PipelineConfig(**kwargs_new)

    @staticmethod
    def _merge_with_nested(obj: Any, overrides: dict) -> Any:
        """递归合并嵌套 dict 到 dataclass 实例。"""
        if not hasattr(obj, "merge"):
            return obj
        nested: dict[str, Any] = {}
        flat: dict[str, Any] = {}
        for k, v in overrides.items():
            if "__" in k:
                outer, inner = k.split("__", 1)
                nested.setdefault(outer, {})[inner] = v
            else:
                flat[k] = v
        merged = obj.merge(**flat, **nested)
        return merged

    def to_dict(self) -> dict:
        """序列化为扁平 dict（含嵌套子配置）。"""
        def _to_plain(obj: Any) -> Any:
            if isinstance(obj, Path):
                return str(obj)
            if isinstance(obj, tuple):
                return list(obj)
            if isinstance(obj, list):
                return [_to_plain(x) for x in obj]
            if hasattr(obj, "__dataclass_fields__"):
                return {k: _to_plain(v) for k, v in asdict(obj).items()}
            return obj
        result: dict[str, Any] = {}
        for name in ("kronos", "ta", "scoring", "scoring_strategy",
                      "risk_boost_strategy", "risk", "filters", "abnormality",
                      "trading", "output", "logging", "retry", "degradation"):
            val = getattr(self, name)
            result[name] = _to_plain(val)
        # 同时输出扁平委托键，保持与旧测试的向后兼容
        _DELEGATES = {
            "sample_count":      ("kronos", "sample_count"),
            "pred_len":          ("kronos", "pred_len"),
            "lookback":          ("kronos", "lookback"),
            "model_name":        ("kronos", "model_name"),
            "device":            ("kronos", "device"),
            "T":                 ("kronos", "T"),
            "top_p":             ("kronos", "top_p"),
            "use_cache":         ("kronos", "use_cache"),
            "llm_provider":      ("ta", "llm_provider"),
            "deep_think_llm":    ("ta", "deep_think_llm"),
            "quick_think_llm":   ("ta", "quick_think_llm"),
            "max_debate_rounds": ("ta", "max_debate_rounds"),
            "output_language":   ("ta", "output_language"),
            "min_confidence":    ("filters", "min_confidence"),
            "allowed_signals":   ("filters", "allowed_signals"),
            "market_cap_range":  ("filters", "market_cap_range"),
            "industry_whitelist": ("filters", "industry_whitelist"),
            "industry_blacklist": ("filters", "industry_blacklist"),
            "pe_range":          ("filters", "pe_range"),
            "pb_range":          ("filters", "pb_range"),
            "max_risk_score":    ("filters", "max_risk_score"),
            "min_volume_ratio":  ("filters", "min_volume_ratio"),
            "min_turnover_rate": ("filters", "min_turnover_rate"),
            "exclude_st":        ("filters", "exclude_st"),
            "skip_new_stock":    ("abnormality", "skip_new_stock"),
            "new_stock_min_days": ("abnormality", "new_stock_min_days"),
            "kline_min_completeness": ("abnormality", "kline_min_completeness"),
            "abnormality_risk_boost_enabled": ("abnormality", "abnormality_risk_boost_enabled"),
            "output_dir":        ("output", "output_dir"),
            "json_path":         ("output", "json_path"),
            "html_path":         ("output", "html_path"),
            "log_level":         ("logging", "log_level"),
            "log_json":          ("logging", "log_json"),
            "retry_max_attempts": ("retry", "retry_max_attempts"),
            "retry_base_delay":  ("retry", "retry_base_delay"),
            "retry_jitter":      ("retry", "retry_jitter"),
            "retry_rate_limit_backoff": ("retry", "retry_rate_limit_backoff"),
            "retry_rate_limit_max_wait": ("retry", "retry_rate_limit_max_wait"),
            "degrade_mode":      ("degradation", "degrade_mode"),
            "ta_cache_fallback_enabled": ("degradation", "ta_cache_fallback_enabled"),
            "ta_cache_max_age_days": ("degradation", "ta_cache_max_age_days"),
            "universe_source":   ("filters", "universe_source"),
        }
        for flat_key, (container, attr) in _DELEGATES.items():
            result[flat_key] = _to_plain(getattr(getattr(self, container), attr))
        return result

    @classmethod
    def from_dict(cls, data: dict) -> "PipelineConfig":
        """从扁平 dict 反序列化。"""
        copy = dict(data)
        # 提取已知的子配置 key
        sub_keys = {
            "kronos", "ta", "scoring", "scoring_strategy", "risk_boost_strategy",
            "risk", "filters", "abnormality", "trading", "output",
            "logging", "retry", "degradation",
        }
        sub_data: dict[str, dict] = {}
        flat_data: dict[str, Any] = {}
        for k, v in copy.items():
            if k in sub_keys and isinstance(v, dict):
                sub_data[k] = v
            else:
                flat_data[k] = v
        return cls(**flat_data, **sub_data)

    @classmethod
    def load(cls, path: str | Path) -> "PipelineConfig":
        p = Path(path)
        suffix = p.suffix.lower()
        if suffix in (".yaml", ".yml"):
            return cls._load_yaml(p)
        elif suffix == ".json":
            return cls._load_json(p)
        else:
            try:
                return cls._load_json(p)
            except Exception:
                return cls._load_yaml(p)

    @classmethod
    def _load_json(cls, path: Path) -> "PipelineConfig":
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)

    @classmethod
    def _load_yaml(cls, path: Path) -> "PipelineConfig":
        try:
            import yaml
        except ImportError:
            raise ImportError("加载 YAML 配置需要 pyyaml 包：pip install pyyaml")
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            raise ValueError(f"YAML 配置应为对象，得到 {type(data).__name__}")
        return cls.from_dict(data)

    def save(self, path: str | Path) -> None:
        p = Path(path)
        data = self.to_dict()
        if p.suffix.lower() in (".yaml", ".yml"):
            import yaml
            with open(p, "w", encoding="utf-8") as f:
                yaml.safe_dump(data, f, allow_unicode=True, default_flow_style=False)
        else:
            with open(p, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

    # ── 属性代理（向后兼容扁平访问）─────────────────────────────────────────────

    @property
    def constraints(self) -> Any:
        """向后兼容：constraints → trading。"""
        return self.trading

    def __getattr__(self, name: str) -> Any:
        """向后兼容：将扁平字段访问委托给子配置。"""
        # 避免递归：只处理已知属性名
        _DELEGATES = {
            "sample_count":      ("kronos", "sample_count"),
            "pred_len":          ("kronos", "pred_len"),
            "lookback":          ("kronos", "lookback"),
            "model_name":        ("kronos", "model_name"),
            "device":            ("kronos", "device"),
            "T":                 ("kronos", "T"),
            "top_p":             ("kronos", "top_p"),
            "use_cache":         ("kronos", "use_cache"),
            "llm_provider":      ("ta", "llm_provider"),
            "deep_think_llm":    ("ta", "deep_think_llm"),
            "quick_think_llm":   ("ta", "quick_think_llm"),
            "max_debate_rounds": ("ta", "max_debate_rounds"),
            "output_language":   ("ta", "output_language"),
            "min_confidence":    ("filters", "min_confidence"),
            "allowed_signals":   ("filters", "allowed_signals"),
            "market_cap_range":  ("filters", "market_cap_range"),
            "industry_whitelist": ("filters", "industry_whitelist"),
            "industry_blacklist": ("filters", "industry_blacklist"),
            "pe_range":          ("filters", "pe_range"),
            "pb_range":          ("filters", "pb_range"),
            "max_risk_score":    ("filters", "max_risk_score"),
            "min_volume_ratio":  ("filters", "min_volume_ratio"),
            "min_turnover_rate": ("filters", "min_turnover_rate"),
            "exclude_st":        ("filters", "exclude_st"),
            "skip_new_stock":    ("abnormality", "skip_new_stock"),
            "new_stock_min_days": ("abnormality", "new_stock_min_days"),
            "kline_min_completeness": ("abnormality", "kline_min_completeness"),
            "abnormality_risk_boost_enabled": ("abnormality", "abnormality_risk_boost_enabled"),
            "output_dir":        ("output", "output_dir"),
            "json_path":         ("output", "json_path"),
            "html_path":         ("output", "html_path"),
            "log_level":         ("logging", "log_level"),
            "log_json":          ("logging", "log_json"),
            "retry_max_attempts": ("retry", "retry_max_attempts"),
            "retry_base_delay":  ("retry", "retry_base_delay"),
            "retry_jitter":      ("retry", "retry_jitter"),
            "retry_rate_limit_backoff": ("retry", "retry_rate_limit_backoff"),
            "retry_rate_limit_max_wait": ("retry", "retry_rate_limit_max_wait"),
            "degrade_mode":      ("degradation", "degrade_mode"),
            "ta_cache_fallback_enabled": ("degradation", "ta_cache_fallback_enabled"),
            "ta_cache_max_age_days": ("degradation", "ta_cache_max_age_days"),
            "universe_source":   ("filters", "universe_source"),
        }
        if name in _DELEGATES:
            container, attr = _DELEGATES[name]
            return getattr(getattr(self, container), attr)
        raise AttributeError(
            f"'{type(self).__name__}' object has no attribute '{name}'"
        )

    def validate(self) -> tuple[list[str], list[str]]:
        """校验所有子配置，返回 (errors, warnings)。"""
        errors: list[str] = []
        warnings: list[str] = []
        for name in ("kronos", "ta", "scoring", "risk", "filters",
                      "abnormality", "trading", "retry", "degradation"):
            sub = getattr(self, name)
            if hasattr(sub, "validate"):
                errs = sub.validate()
                errors.extend(errs)
        # 语义警告：ta_cache_fallback_enabled 与 degrade_mode 不匹配
        if self.degradation.ta_cache_fallback_enabled and \
                self.degradation.degrade_mode != "ta_cache_fallback":
            warnings.append(
                f"TA_CACHE_FALLBACK_ENABLED=true 但 DEGRADE_MODE="
                f"{self.degradation.degrade_mode}，"
                f"TA 缓存回退仅在 degrade_mode=ta_cache_fallback 时生效"
            )
        return errors, warnings
