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
"""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from trade_krono_cli.config import Settings, get_settings
from trade_krono_cli.version import (
    compute_config_hash,
    get_kronos_model_version,
    get_llm_version,
    get_ta_prompt_version,
)

# ═════════════════════════════════════════════════════════════════════════════
#  Git 工具
# ════════════════════════════════════════════════════════════════════════════


def _git_sha(repo_path: Path) -> tuple[str | None, str | None]:
    """返回 (full_sha, short_sha)；路径不存在或非 git repo 时返回 (None, None)。"""
    if not (repo_path / ".git").exists():
        return None, None
    try:
        rc, full, _ = (
            subprocess.run(
                ["git", "-C", str(repo_path), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                timeout=5,
            ).returncode,
            subprocess.run(
                ["git", "-C", str(repo_path), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout.strip(),
            "",
        )
        rc2, short, _ = (
            subprocess.run(
                ["git", "-C", str(repo_path), "rev-parse", "--short=12", "HEAD"],
                capture_output=True,
                text=True,
                timeout=5,
            ).returncode,
            subprocess.run(
                ["git", "-C", str(repo_path), "rev-parse", "--short=12", "HEAD"],
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout.strip(),
            "",
        )
        if rc == 0:
            return full, (short if rc2 == 0 else full[:12])
    except Exception as e:
        logger.debug(f"git commit hash 检测失败: {e}")
    return None, None


def _git_dirty(repo_path: Path) -> bool:
    """判断 git 仓库是否有未提交的修改。"""
    if not (repo_path / ".git").exists():
        return False
    try:
        rc, out, _ = (
            subprocess.run(
                ["git", "-C", str(repo_path), "status", "--porcelain"],
                capture_output=True,
                text=True,
                timeout=5,
            ).returncode,
            subprocess.run(
                ["git", "-C", str(repo_path), "status", "--porcelain"],
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout,
            "",
        )
        return bool(out.strip()) if rc == 0 else False
    except Exception as e:
        logger.debug(f"git 脏检测失败: {e}")
        return False


# ═════════════════════════════════════════════════════════════════════════════
#  数据类
# ════════════════════════════════════════════════════════════════════════════


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
    allowed_signals: tuple = ("BUY", "OVERWEIGHT", "HOLD")
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

    def __post_init__(self):
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


# ═════════════════════════════════════════════════════════════════════════════
#  构建器
# ════════════════════════════════════════════════════════════════════════════


def _build_code_artifact(project_root: Path) -> CodeArtifact:
    """扫描 trade-krono-cli / TradingAgents / Kronos 的 git 状态。"""

    def _repo_info(rel_path: str) -> dict:
        repo_path = project_root / rel_path
        full, short = _git_sha(repo_path)
        dirty = _git_dirty(repo_path) if full else False
        return {"commit": full, "commit_short": short, "dirty": dirty} if full else {}

    return CodeArtifact(
        trade_krono_cli=_repo_info("trade_krono_cli"),
        tradingagents=_repo_info("external/TradingAgents-astock"),
        kronos=_repo_info("external/Kronos"),
    )


def _build_model_artifact(settings: Settings) -> ModelArtifact:
    """收集 Kronos 模型信息。"""
    torch_ver, cuda_avail, cuda_ver, gpu_model = None, False, None, None
    try:
        import torch

        torch_ver = torch.__version__
        cuda_avail = torch.cuda.is_available()
        if cuda_avail:
            cuda_ver = torch.version.cuda
            gpu_model = torch.cuda.get_device_name(0) if torch.cuda.device_count() > 0 else None
    except ImportError:
        pass

    return ModelArtifact(
        name=settings.kronos_model,
        tokenizer=settings.kronos_tokenizer,
        device=settings.kronos_device,
        sample_count=settings.kronos_sample_count,
        pred_len=settings.kronos_pred_len,
        torch_version=torch_ver,
        cuda_available=cuda_avail,
        cuda_version=cuda_ver,
        gpu_model=gpu_model,
    )


def _build_llm_artifact(settings: Settings) -> LlmArtifact:
    """收集 LLM 配置信息（provider / 模型名 / backend URL）。"""
    return LlmArtifact(
        provider=settings.llm_provider,
        deep_think_model=settings.deep_think_llm,
        quick_think_model=settings.quick_think_llm,
        backend_url=settings.backend_url,
    )


def _build_data_artifact(settings: Settings) -> DataArtifact:
    """从数据源工厂获取最新数据日期。"""
    source = settings.data_provider
    latest_date: str | None = None
    try:
        from trade_krono_cli.data_providers import get_data_factory

        factory = get_data_factory()
        provider = factory.get_provider(source)
        if provider is not None and hasattr(provider, "get_latest_date"):
            latest_date = provider.get_latest_date("sh.600519")  # 用茅台试探
    except Exception as e:
        logger.debug(f"数据版本探测跳过: {e}")
    return DataArtifact(source=source, latest_date=latest_date)


def _build_prompt_artifact(settings: Settings) -> PromptArtifact:
    """从 Settings 构建 PromptArtifact（辩论轮次 / 语言 / 结构化输出）。"""
    return PromptArtifact(
        max_debate_rounds=settings.max_debate_rounds,
        max_risk_discuss_rounds=settings.max_risk_discuss_rounds,
        output_language=settings.output_language,
        structured_output=True,
    )


def _build_strategy_artifact(settings: Settings) -> StrategyArtifact:
    """从 Settings 构建 StrategyArtifact（评分策略 / 风险策略 / 阈值）。"""
    return StrategyArtifact(
        scoring_strategy=settings.scoring_strategy,
        risk_boost_strategy=settings.risk_boost_strategy,
        min_confidence=settings.default_min_confidence,
        allowed_signals=tuple(settings.default_allowed_signals),
        config_hash=compute_config_hash(settings),
    )


def build_manifest(
    settings: Settings | None = None,
    project_root: Path | None = None,
) -> ArtifactManifest:
    """构建完整的 ArtifactManifest。

    Parameters
    ----------
    settings    : Settings 实例（None 时使用全局单例）
    project_root : 项目根目录（None 时使用 settings.project_root）

    Returns
    -------
    ArtifactManifest

    """
    s = settings or get_settings()
    root = project_root or s.project_root

    logger.debug("🔍 构建 ArtifactManifest...")
    return ArtifactManifest(
        code=_build_code_artifact(root),
        model=_build_model_artifact(s),
        llm=_build_llm_artifact(s),
        data=_build_data_artifact(s),
        prompt=_build_prompt_artifact(s),
        strategy=_build_strategy_artifact(s),
        environment=EnvironmentArtifact(),
    )


# ═════════════════════════════════════════════════════════════════════════════
#  artifact.lock 读写
# ════════════════════════════════════════════════════════════════════════════

_ARTIFACT_LOCK_FILENAME = "artifact.lock"


def _artifact_lock_path(project_root: Path | None = None) -> Path:
    root = project_root or Path(__file__).resolve().parent.parent
    return root / "external" / _ARTIFACT_LOCK_FILENAME


def load_artifact_lock(project_root: Path | None = None) -> list[dict]:
    """加载 artifact.lock，返回条目列表（旧格式兼容）。"""
    lock_path = _artifact_lock_path(project_root)
    if not lock_path.exists():
        return []
    try:
        with open(lock_path, encoding="utf-8") as f:
            data = json.load(f)
        # 支持两种格式：数组 或 {"entries": [...]}
        if isinstance(data, list):
            return data
        return data.get("entries", [])
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"⚠️  artifact.lock 读取失败: {e}")
        return []


def save_artifact_lock(
    entries: list[dict],
    project_root: Path | None = None,
) -> Path:
    """保存 artifact.lock（追加模式：先 load，append，再 save）。"""
    lock_path = _artifact_lock_path(project_root)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    existing = load_artifact_lock(project_root)
    combined = existing + entries
    with open(lock_path, "w", encoding="utf-8") as f:
        json.dump({"schema_version": "2.0", "entries": combined}, f, indent=2, ensure_ascii=False)
    logger.debug(f"💾 artifact.lock 已更新: {len(combined)} 条记录")
    return lock_path


def append_artifact(
    manifest: ArtifactManifest,
    experiment_id: str | None = None,
    run_id: str | None = None,
    job_id: str | None = None,
    project_root: Path | None = None,
) -> dict:
    """将一次实验的 artifact 追加到 artifact.lock。

    Returns
    -------
    dict : 写入的条目（含 experiment_id）

    """
    eid = experiment_id or manifest.experiment_id()
    entry = {
        "experiment_id": eid,
        "run_id": run_id,
        "job_id": job_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "manifest": manifest.to_dict(),
        "summary": manifest.summary(),
    }
    save_artifact_lock([entry], project_root)
    return entry


def lookup_experiment(experiment_id: str, project_root: Path | None = None) -> dict | None:
    """按 experiment_id 查找历史记录。"""
    for entry in load_artifact_lock(project_root):
        if entry.get("experiment_id") == experiment_id:
            return entry
    return None


# ═════════════════════════════════════════════════════════════════════════════
#  CLI helpers
# ════════════════════════════════════════════════════════════════════════════


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
    from loguru import logger

    logger.info(describe(manifest))
