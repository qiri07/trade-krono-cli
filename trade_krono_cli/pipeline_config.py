"""
PipelineConfig — 流水线集中配置数据类。

将所有散落的参数（约束、Kronos、风险、输出路径、日志级别）汇总到单一配置对象，
支持从 YAML/JSON 文件加载，也支持代码直接构造。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from trade_krono_cli.constraints_config import ConstraintConfig
from trade_krono_cli.config import get_settings


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
            risk_threshold=20.0,
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

    # ── 风险引擎 ────────────────────────────────────────────
    risk_threshold: float = 30.0

    # ── 过滤 ────────────────────────────────────────────────
    min_confidence: float = 55.0
    allowed_signals: tuple[str, ...] = field(default=("BUY", "HOLD"))

    # ── 输出 ────────────────────────────────────────────────
    output_dir: Path = field(default_factory=lambda: Path("outputs"))
    json_path: str = "outputs/results.json"
    html_path: str = "outputs/report.html"

    # ── 日志 ────────────────────────────────────────────────
    log_level: str = "INFO"
    log_json: bool = False  # 是否输出 JSON 结构化日志

    # ── 缓存 ────────────────────────────────────────────────
    no_cache: bool = False

    @classmethod
    def default(cls) -> "PipelineConfig":
        """使用 Settings 默认值构建配置。"""
        s = get_settings()
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
            risk_threshold=30.0,
            min_confidence=s.default_min_confidence,
            allowed_signals=tuple(s.default_allowed_signals),
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
        return PipelineConfig(**current)

    def to_dict(self) -> dict:
        """序列化为可 JSON 序列化的 dict。"""
        d = asdict(self)
        d["output_dir"] = str(self.output_dir)
        # tuple → list（YAML/JSON 序列化需要）
        if isinstance(d.get("allowed_signals"), tuple):
            d["allowed_signals"] = list(d["allowed_signals"])
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "PipelineConfig":
        """从 dict 反序列化。"""
        copy = dict(data)
        if "output_dir" in copy and isinstance(copy["output_dir"], str):
            copy["output_dir"] = Path(copy["output_dir"])
        if "constraints" in copy and isinstance(copy["constraints"], dict):
            copy["constraints"] = ConstraintConfig(**copy["constraints"])
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
            with open(p, "w", encoding="utf-8") as f:
                yaml.dump(data, f, allow_unicode=True, default_flow_style=False)
        else:
            with open(p, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
