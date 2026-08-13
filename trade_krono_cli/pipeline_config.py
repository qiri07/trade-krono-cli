"""
PipelineConfig — 流水线集中配置数据类。

将所有散落的参数（约束、Kronos、风险、输出路径、日志级别）汇总到单一配置对象，
支持从 YAML/JSON 文件加载，也支持代码直接构造。

参数优先级（高 → 低）：
  1. CLI 命令行参数（typer.Option）
  2. 环境变量 / .env
  3. PipelineConfig（YAML/JSON 文件）
  4. 各模块默认值
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from trade_krono_cli.configs.schema import (
    ConstraintConfig as SchemaConstraintConfig,
    RiskConfig as SchemaRiskConfig,
    ScoringConfig as SchemaScoringConfig,
    ScoringStrategyConfig,
    RiskBoostStrategyConfig,
)
from trade_krono_cli.constraints_config import ConstraintConfig
from trade_krono_cli.config import get_settings, Settings
from trade_krono_cli.stock_filter import FilterRule


# ── 配置解析辅助函数 ─────────────────────────────────────────────────────────

def _parse_range(s: str) -> tuple[float, float] | None:
    """解析格式为 \"low,high\" 的字符串，返回 (low, high) 或 None。"""
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
    """解析逗号分隔字符串，返回去重非空列表。"""
    if not s or not s.strip():
        return []
    return [p.strip() for p in s.split(",") if p.strip()]


def _parse_float(s: str) -> float | None:
    """解析浮点数字符串，返回 None 表示未设置。"""
    if not s or not s.strip():
        return None
    try:
        return float(s.strip())
    except ValueError:
        return None


@dataclass
class PipelineConfig:
    """
    完整流水线配置。

    用法：
        # 默认配置
        cfg = PipelineConfig.default()

        # 从 YAML/JSON 文件加载
        cfg = PipelineConfig.load("config.yaml")

        # 部分覆盖
        cfg = PipelineConfig.default().override(
            sample_count=10,
            min_confidence=40.0,
        )
    """

    # ── A 股交易约束 ────────────────────────────────────────
    constraints: ConstraintConfig = field(default_factory=ConstraintConfig)

    # ── Kronos 预测 ─────────────────────────────────────────
    sample_count: int = 5
    pred_len: int = 30
    lookback: int = 400
    model_name: str = "kronos-base"
    device: str = "cpu"
    T: float = 1.0
    top_p: float = 0.9
    use_cache: bool = True

    # ── TA 分析 ─────────────────────────────────────────────
    llm_provider: str = "deepseek"
    deep_think_llm: str = "deepseek-chat"
    quick_think_llm: str = "deepseek-chat"
    max_debate_rounds: int = 1
    output_language: str = "Chinese"

    # ── 综合打分配置 ────────────────────────────────────────
    scoring: SchemaScoringConfig = field(default_factory=SchemaScoringConfig)
    scoring_strategy: ScoringStrategyConfig = field(default_factory=ScoringStrategyConfig)

    # ── 风险加分策略配置 ────────────────────────────────────────
    risk_boost_strategy: RiskBoostStrategyConfig = field(default_factory=RiskBoostStrategyConfig)

    # ── 风险引擎配置 ────────────────────────────────────────
    risk: SchemaRiskConfig = field(default_factory=SchemaRiskConfig)

    # ── 过滤 ────────────────────────────────────────────────
    min_confidence: float = 55.0
    allowed_signals: tuple[str, ...] = field(default=("BUY", "HOLD"))
    # ── 股票过滤 ─────────────────────────────────────────────
    market_cap_range: tuple[float, float] | None = None
    industry_whitelist: list[str] = field(default_factory=list)
    industry_blacklist: list[str] = field(default_factory=list)
    pe_range: tuple[float, float] | None = None
    pb_range: tuple[float, float] | None = None
    max_risk_score: float | None = None
    min_volume_ratio: float | None = None
    min_turnover_rate: float | None = None
    exclude_st: bool = True
    filter_rules: list[FilterRule] = field(default_factory=list)
    # ── 异常股票处理 ──────────────────────────────────────────
    skip_new_stock: bool = True
    new_stock_min_days: int = 60
    kline_min_completeness: float = 0.85
    abnormality_risk_boost_enabled: bool = True

    # ── 输出 ────────────────────────────────────────────────
    output_dir: Path = field(default_factory=lambda: Path("outputs"))
    json_path: str = "outputs/results.json"
    html_path: str = "outputs/report.html"

    # ── 日志 ────────────────────────────────────────────────
    log_level: str = "INFO"
    log_json: bool = False  # 是否输出 JSON 结构化日志

    # ── 重试策略 ──────────────────────────────────────────
    retry_max_attempts: int = 3
    retry_base_delay: float = 2.0
    retry_jitter: bool = True
    retry_rate_limit_backoff: bool = True
    retry_rate_limit_max_wait: float = 60.0

    # ── 降级策略 ──────────────────────────────────────────
    degrade_mode: str = "strict"
    """降级策略：strict / ta_only_on_kronos_fail / ta_cache_fallback"""
    ta_cache_fallback_enabled: bool = False
    """是否允许在 TA 失败时回退到最近一次缓存的 TA 结果。"""
    ta_cache_max_age_days: int = 7
    """TA 缓存结果最大有效期（天）。"""

    # ── 缓存 ────────────────────────────────────────────────
    no_cache: bool = False

    @classmethod
    def default(cls, settings: Optional[Settings] = None) -> "PipelineConfig":
        """使用 Settings 默认值构建配置。"""
        s = settings or get_settings()
        return cls(
            constraints=ConstraintConfig(),
            sample_count=s.kronos_sample_count,
            pred_len=s.kronos_pred_len,
            lookback=s.kronos_lookback,
            model_name=s.kronos_model,
            device=s.kronos_device,
            T=s.kronos_T,
            top_p=s.kronos_top_p,
            use_cache=not s.kronos_sample_count == 0,
            llm_provider=s.llm_provider,
            deep_think_llm=s.deep_think_llm,
            quick_think_llm=s.quick_think_llm,
            max_debate_rounds=s.max_debate_rounds,
            output_language=s.output_language,
            scoring=SchemaScoringConfig(),
            scoring_strategy=ScoringStrategyConfig(
                strategy=s.scoring_strategy,
            ),
            risk_boost_strategy=RiskBoostStrategyConfig(
                strategy=s.risk_boost_strategy,
                multiplier=s.risk_boost_multiplier,
                diminishing_power=s.risk_boost_diminishing_power,
            ),
            risk=SchemaRiskConfig(),
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
            skip_new_stock=s.filter_skip_new_stock,
            new_stock_min_days=s.filter_new_stock_min_days,
            kline_min_completeness=s.filter_kline_min_completeness,
            abnormality_risk_boost_enabled=s.filter_abnormality_risk_boost_enabled,
            retry_max_attempts=s.retry_max_attempts,
            retry_base_delay=s.retry_base_delay,
            retry_jitter=s.retry_jitter,
            retry_rate_limit_backoff=s.retry_rate_limit_backoff,
            retry_rate_limit_max_wait=s.retry_rate_limit_max_wait,
            degrade_mode=s.degrade_mode,
            ta_cache_fallback_enabled=s.ta_cache_fallback_enabled,
            ta_cache_max_age_days=s.ta_cache_max_age_days,
            output_dir=s.results_dir.parent,
        )

    def override(self, **kwargs) -> "PipelineConfig":
        """返回一个新的 PipelineConfig，部分字段被覆盖。"""
        current = asdict(self)
        current.update(kwargs)
        # 重新构造嵌套对象
        if "constraints" in kwargs and isinstance(kwargs["constraints"], dict):
            current["constraints"] = ConstraintConfig(**kwargs["constraints"])
        elif "constraints" not in kwargs:
            current["constraints"] = self.constraints
        if "scoring_strategy" in kwargs and isinstance(kwargs["scoring_strategy"], dict):
            base = self.scoring_strategy
            current["scoring_strategy"] = ScoringStrategyConfig(
                strategy=kwargs["scoring_strategy"].get("strategy", base.strategy),
                params=kwargs["scoring_strategy"].get("params", base.params),
            )
        elif "scoring_strategy" not in kwargs:
            current["scoring_strategy"] = self.scoring_strategy
        if "risk_boost_strategy" in kwargs and isinstance(kwargs["risk_boost_strategy"], dict):
            base = self.risk_boost_strategy
            rb_override = kwargs["risk_boost_strategy"]
            current["risk_boost_strategy"] = RiskBoostStrategyConfig(
                strategy=rb_override.get("strategy", base.strategy),
                multiplier=rb_override.get("multiplier", base.multiplier),
                diminishing_power=rb_override.get("diminishing_power", base.diminishing_power),
            )
        elif "risk_boost_strategy" not in kwargs:
            current["risk_boost_strategy"] = self.risk_boost_strategy
        return PipelineConfig(**current)

    def to_dict(self) -> dict:
        """序列化为可 JSON 序列化的 dict。"""
        d = asdict(self)
        d["output_dir"] = str(self.output_dir)
        # tuple → list（YAML/JSON 序列化需要）
        if isinstance(d.get("allowed_signals"), tuple):
            d["allowed_signals"] = list(d["allowed_signals"])
        # 递归转换嵌套 dict 中的 tuple（如 breakpoints）
        self._convert_tuples_to_lists(d)
        return d

    @staticmethod
    def _convert_tuples_to_lists(obj):
        """递归将 dict/list 中的 tuple 转为 list，使 YAML/JSON 可序列化。"""
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(v, tuple):
                    obj[k] = list(v)
                elif isinstance(v, (dict, list)):
                    PipelineConfig._convert_tuples_to_lists(v)
        elif isinstance(obj, list):
            for item in obj:
                if isinstance(item, (dict, list)):
                    PipelineConfig._convert_tuples_to_lists(item)

    @classmethod
    def from_dict(cls, data: dict) -> "PipelineConfig":
        """从 dict 反序列化。"""
        copy = dict(data)
        if "output_dir" in copy and isinstance(copy["output_dir"], str):
            copy["output_dir"] = Path(copy["output_dir"])
        if "constraints" in copy and isinstance(copy["constraints"], dict):
            copy["constraints"] = ConstraintConfig(**copy["constraints"])
        if "scoring" in copy and isinstance(copy["scoring"], dict):
            copy["scoring"] = SchemaScoringConfig(**copy["scoring"])
        if "scoring_strategy" in copy and isinstance(copy["scoring_strategy"], dict):
            copy["scoring_strategy"] = ScoringStrategyConfig(**copy["scoring_strategy"])
        if "risk_boost_strategy" in copy and isinstance(copy["risk_boost_strategy"], dict):
            copy["risk_boost_strategy"] = RiskBoostStrategyConfig(**copy["risk_boost_strategy"])
        if "risk" in copy and isinstance(copy["risk"], dict):
            copy["risk"] = SchemaRiskConfig(**copy["risk"])
        return cls(**copy)

    @classmethod
    def load(cls, path: str | Path) -> "PipelineConfig":
        """
        从 YAML 或 JSON 文件加载配置。

        支持格式：
          - .yaml / .yml → PyYAML（需要安装 yaml 模块）
          - .json → json.load
          - 无扩展名 → 优先 JSON，失败后尝试 YAML
        """
        p = Path(path)
        suffix = p.suffix.lower()

        if suffix in (".yaml", ".yml"):
            return cls._load_yaml(p)
        elif suffix == ".json":
            return cls._load_json(p)
        else:
            # 尝试 JSON，失败再试 YAML
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
            import yaml  # type: ignore
        except ImportError:
            raise ImportError(
                "加载 YAML 配置需要 pyyaml 包：pip install pyyaml"
            )
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            raise ValueError(f"YAML 配置应为对象，得到 {type(data).__name__}")
        return cls.from_dict(data)

    def save(self, path: str | Path) -> None:
        """将当前配置序列化保存到文件。"""
        p = Path(path)
        data = self.to_dict()
        if p.suffix.lower() in (".yaml", ".yml"):
            import yaml  # type: ignore
            # 使用 safe_dump 避免生成 Python 专有标签（如 !!python/tuple）
            with open(p, "w", encoding="utf-8") as f:
                yaml.safe_dump(data, f, allow_unicode=True, default_flow_style=False)
        else:
            with open(p, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
