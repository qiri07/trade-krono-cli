"""artifact_manifest — 实验可复现性清单（Experiment Artifact Manifest）。

将"这次分析用了什么"从散落的多个字段合并成一个结构化的、可序列化、
可比较的 artifact manifest。每个 experiment 有唯一 ID，对应一份完整清单。

维度（按 AI 自动优化所需的粒度）：
  code       — trade-krono-cli / TradingAgents / Kronos 的 git commit
  model      — kronos 模型名 / tokenizer / device / PyTorch / CUDA
  llm        — provider / deep_model / quick_model
  data       — 数据源 / 最新快照日期
  prompt     — TA 提示词参数组合
  strategy   — 评分策略 / 风险策略 / 过滤参数
  environment — Python / 平台 / 主机

文件：
  external/artifact.lock  — 机器维护的最终锁定版本（每次 run 追加新条目）
  external/artifact.yaml  — 人类可编辑的参考基线（可选）

关联：
  experiment_id = sha256(manifest_json) → 唯一标识一次完整配置快照
  每次 run 写入一条 artifact 记录到 research_db.jobs 表

子模块：
  types     — ArtifactManifest 及子类型 dataclass
  builders  — 构建函数（build_manifest 等）
  git_utils — Git 状态检测工具
  lock      — artifact.lock 文件读写
"""

from __future__ import annotations

from loguru import logger

from trade_krono_cli.artifact_manifest.builders import (
    _build_code_artifact,
    _build_data_artifact,
    _build_llm_artifact,
    _build_model_artifact,
    _build_prompt_artifact,
    _build_strategy_artifact,
    build_manifest,
)
from trade_krono_cli.artifact_manifest.git_utils import _git_dirty, _git_sha
from trade_krono_cli.artifact_manifest.lock import (
    append_artifact,
    load_artifact_lock,
    lookup_experiment,
    save_artifact_lock,
)
from trade_krono_cli.artifact_manifest.types import (
    ArtifactManifest,
    CodeArtifact,
    DataArtifact,
    EnvironmentArtifact,
    LlmArtifact,
    ModelArtifact,
    PromptArtifact,
    StrategyArtifact,
)

__all__ = [
    "ArtifactManifest",
    "CodeArtifact",
    "ModelArtifact",
    "LlmArtifact",
    "DataArtifact",
    "PromptArtifact",
    "StrategyArtifact",
    "EnvironmentArtifact",
    "_git_sha",
    "_git_dirty",
    "_build_code_artifact",
    "_build_model_artifact",
    "_build_llm_artifact",
    "_build_data_artifact",
    "_build_prompt_artifact",
    "_build_strategy_artifact",
    "build_manifest",
    "load_artifact_lock",
    "save_artifact_lock",
    "append_artifact",
    "lookup_experiment",
    "describe",
    "print_manifest",
    "subprocess",
]


def describe(manifest: ArtifactManifest | None = None) -> str:
    """返回人类可读的 manifest 摘要。"""
    m = manifest or build_manifest()
    lines = [
        f"experiment_id  : {m.experiment_id()}",
        "",
        "  code:",
        f"    trade-krono-cli : {m.code.trade_krono_cli.get('commit_short', '?')}",
        f"    tradingagents   : {m.code.tradingagents.get('commit_short', '?')}",
        f"    kronos          : {m.code.kronos.get('commit_short', '?')}",
        "",
        "  model:",
        f"    {m.model.name} / {m.model.tokenizer} / {m.model.device}",
        f"    torch={m.model.torch_version or '?'}  cuda={'yes' if m.model.cuda_available else 'no'}",
        f"    sample_count={m.model.sample_count}  pred_len={m.model.pred_len}",
        "",
        "  llm:",
        f"    {m.llm.provider}/{m.llm.deep_think_model}",
        "",
        "  data:",
        f"    source={m.data.source}  latest={m.data.latest_date or '?'}",
        "",
        "  prompt:",
        f"    {m.prompt.version_tag}",
        "",
        "  strategy:",
        f"    scoring={m.strategy.scoring_strategy}  risk={m.strategy.risk_boost_strategy}",
        f"    min_confidence={m.strategy.min_confidence}  signals={m.strategy.allowed_signals}",
        f"    config_hash={m.strategy.config_hash[:12]}",
        "",
        "  environment:",
        f"    python={m.environment.python_version}  {m.environment.platform_system}/{m.environment.platform_machine}",
    ]
    return "\n".join(lines)


def print_manifest(manifest: ArtifactManifest | None = None) -> None:
    """将 manifest 摘要以 INFO 级别打印到日志。"""

    logger.info(describe(manifest))
