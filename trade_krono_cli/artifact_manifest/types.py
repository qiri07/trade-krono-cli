"""artifact_manifest.types — 实验可复现性清单数据结构。"""

from __future__ import annotations

import hashlib
import json
import platform
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from loguru import logger

from trade_krono_cli.version import (
    get_kronos_model_version,
    get_llm_version,
    get_ta_prompt_version,
)


@dataclass(frozen=True)
class CodeArtifact:
    """代码版本。"""

    trade_krono_cli: dict = field(default_factory=dict)
    """{"commit": str, "commit_short": str, "dirty": bool}"""
    tradingagents: dict = field(default_factory=dict)
    kronos: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ModelArtifact:
    """模型版本。"""

    name: str = "kronos-base"
    tokenizer: str = "kronos-Tokenizer-base"
    device: str = "cpu"
    sample_count: int = 5
    pred_len: int = 30
    torch_version: str | None = None
    cuda_available: bool = False
    cuda_version: str | None = None
    gpu_model: str | None = None

    @property
    def version_tag(self) -> str:
        """Kronos 模型版本标签（含模型名、tokenizer、device）。"""
        return get_kronos_model_version(self.name, self.tokenizer, self.device)


@dataclass(frozen=True)
class LlmArtifact:
    """LLM 版本。"""

    provider: str = "deepseek"
    deep_think_model: str = "deepseek-chat"
    quick_think_model: str = "deepseek-chat"
    backend_url: str | None = None

    @property
    def version_tag(self) -> str:
        """LLM provider 版本标签（含 provider、deep_think_model、quick_think_model）。"""
        return get_llm_version(self.provider, self.deep_think_model, self.quick_think_model)


@dataclass(frozen=True)
class DataArtifact:
    """数据版本。"""

    source: str = "baostock"
    latest_date: str | None = None
    """数据源中最新一条 K 线的日期（用于标识数据新鲜度）。"""


@dataclass(frozen=True)
class PromptArtifact:
    """提示词版本。"""

    max_debate_rounds: int = 1
    max_risk_discuss_rounds: int = 1
    output_language: str = "Chinese"
    structured_output: bool = True

    @property
    def version_tag(self) -> str:
        """TA 提示词版本标签（含辩论轮次、风险讨论轮次、语言、结构化输出配置）。"""
        return get_ta_prompt_version(
            self.max_debate_rounds,
            self.max_risk_discuss_rounds,
            self.output_language,
            self.structured_output,
        )


@dataclass(frozen=True)
class StrategyArtifact:
    """策略配置版本。"""

    scoring_strategy: str = "linear"
    risk_boost_strategy: str = "fixed_boost"
    min_confidence: float = 55.0
    allowed_signals: tuple = field(default_factory=lambda: ("BUY", "OVERWEIGHT", "HOLD"))
    config_hash: str = ""
    """compute_config_hash() 的结果，用于区分不同策略配置。"""


@dataclass(frozen=True)
class EnvironmentArtifact:
    """运行环境。"""

    python_version: str = platform.python_version()
    platform_system: str = platform.system()
    platform_machine: str = platform.machine()
    hostname: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def __post_init__(self) -> None:
        if not self.hostname:
            try:
                object.__setattr__(self, "hostname", platform.node() or "")
            except Exception as e:
                logger.debug(f"获取主机名失败，使用空字符串: {e}")
                object.__setattr__(self, "hostname", "")


@dataclass(frozen=True)
class ArtifactManifest:
    """完整实验可复现性清单。

    所有字段均为 frozen dataclass，可直接作为 dict 序列化或用于计算 hash。
    experiment_id = sha256(json.dumps(manifest.to_dict(), sort_keys=True))
    """

    code: CodeArtifact = field(default_factory=CodeArtifact)
    model: ModelArtifact = field(default_factory=ModelArtifact)
    llm: LlmArtifact = field(default_factory=LlmArtifact)
    data: DataArtifact = field(default_factory=DataArtifact)
    prompt: PromptArtifact = field(default_factory=PromptArtifact)
    strategy: StrategyArtifact = field(default_factory=StrategyArtifact)
    environment: EnvironmentArtifact = field(default_factory=EnvironmentArtifact)

    def to_dict(self) -> dict:
        """将完整 manifest 序列化为字典（含所有子 artiface 的字段）。"""
        return asdict(self)

    def experiment_id(self) -> str:
        """基于完整 manifest 计算唯一实验 ID。

        相同的 manifest → 相同的 experiment_id（跨机器、跨时间稳定）。
        """
        raw = json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def summary(self) -> dict:
        """返回精简摘要（不含 config_hash 等长字段）。"""
        d = self.to_dict()
        d["experiment_id"] = self.experiment_id()
        d["strategy"]["config_hash"] = (
            self.strategy.config_hash[:12] if self.strategy.config_hash else ""
        )
        return d
