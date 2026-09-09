"""PipelineConfig IO — 序列化/反序列化与文件读写。"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from trade_krono_cli.pipeline_config import PipelineConfig


def _to_plain(obj: Any) -> Any:  # noqa: ANN401 — 递归序列化，输入类型未知（dataclass / BaseModel / 原生类型）
    """将对象递归序列化为 JSON 兼容的纯 Python 类型。"""
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, tuple):
        return list(obj)
    if isinstance(obj, list):
        return [_to_plain(x) for x in obj]
    if hasattr(obj, "__dataclass_fields__"):
        return {k: _to_plain(v) for k, v in asdict(obj).items()}
    return obj


def to_dict(cfg: "PipelineConfig") -> dict:
    """将 PipelineConfig 序列化为扁平 dict（含嵌套子配置）。"""
    result: dict[str, Any] = {}
    for name in (
        "kronos",
        "ta",
        "scoring",
        "scoring_strategy",
        "risk_boost_strategy",
        "risk",
        "filters",
        "abnormality",
        "trading",
        "output",
        "logging",
        "retry",
        "degradation",
    ):
        val = getattr(cfg, name)
        result[name] = _to_plain(val)

    # 同时输出扁平委托键，保持与旧测试的向后兼容
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
    for flat_key, (container, attr) in _DELEGATES.items():
        result[flat_key] = _to_plain(getattr(getattr(cfg, container), attr))
    return result


def from_dict(data: dict) -> "PipelineConfig":
    """从扁平 dict 反序列化为 PipelineConfig。"""
    from trade_krono_cli.pipeline_config import PipelineConfig

    copy = dict(data)
    # 提取已知的子配置 key
    sub_keys = {
        "kronos",
        "ta",
        "scoring",
        "scoring_strategy",
        "risk_boost_strategy",
        "risk",
        "filters",
        "abnormality",
        "trading",
        "output",
        "logging",
        "retry",
        "degradation",
    }
    sub_data: dict[str, dict] = {}
    flat_data: dict[str, Any] = {}
    for k, v in copy.items():
        if k in sub_keys and isinstance(v, dict):
            sub_data[k] = v
        else:
            flat_data[k] = v
    return PipelineConfig(**dict(flat_data), **dict(sub_data))  # type: ignore[arg-type]


def load(path: str | Path) -> "PipelineConfig":
    """从 JSON 或 YAML 文件加载 PipelineConfig。"""
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix in (".yaml", ".yml"):
        return _load_yaml(p)
    if suffix == ".json":
        return _load_json(p)
    try:
        return _load_json(p)
    except Exception as e:
        logger.debug(f"⚠️  JSON 解析失败，尝试 YAML: {e}")
        return _load_yaml(p)


def _load_json(path: Path) -> "PipelineConfig":
    """从 JSON 文件加载 PipelineConfig。"""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return from_dict(data)


def _load_yaml(path: Path) -> "PipelineConfig":
    """从 YAML 文件加载 PipelineConfig。"""
    try:
        import yaml
    except ImportError:
        msg = "加载 YAML 配置需要 pyyaml 包：pip install pyyaml"
        raise ImportError(msg) from None
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        msg = f"YAML 配置应为对象，得到 {type(data).__name__}"
        raise ValueError(msg)
    return from_dict(data)


def save(cfg: "PipelineConfig", path: str | Path) -> None:
    """将 PipelineConfig 保存为 JSON 或 YAML 文件。"""
    p = Path(path)
    data = to_dict(cfg)
    if p.suffix.lower() in (".yaml", ".yml"):
        import yaml

        with open(p, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True, default_flow_style=False)
    else:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
