"""artifact_manifest.builders — ArtifactManifest 构建器。"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from trade_krono_cli.config import Settings, get_settings
from trade_krono_cli.version import compute_config_hash

from .git_utils import _git_dirty, _git_sha
from .types import (
    ArtifactManifest,
    CodeArtifact,
    DataArtifact,
    EnvironmentArtifact,
    LlmArtifact,
    ModelArtifact,
    PromptArtifact,
    StrategyArtifact,
)


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
