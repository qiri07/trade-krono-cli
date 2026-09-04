"""external/config_io.py — 外部 repo 配置文件读写。

包含 lock 文件（repo.lock）和 YAML 配置（repos.yaml）的读写函数。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from loguru import logger

from trade_krono_cli.config import Settings, get_settings
from trade_krono_cli.external.models import ExternalRepo

_LOCK_FILENAME = "repo.lock"
_CONFIG_FILENAME = "repos.yaml"


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _lock_path(project_root: Path | None = None) -> Path:
    root = project_root or _project_root()
    return root / "external" / _LOCK_FILENAME


def _config_path(project_root: Path | None = None) -> Path:
    root = project_root or _project_root()
    return root / "external" / _CONFIG_FILENAME


def load_lock(project_root: Path | None = None) -> dict:
    """加载 repo.lock 文件。文件不存在时返回空 dict。"""
    lock_path = _lock_path(project_root)
    if not lock_path.exists():
        return {}
    try:
        with open(lock_path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"⚠️  repo.lock 读取失败: {e}")
        return {}


def save_lock(data: dict, project_root: Path | None = None) -> None:
    """保存 repo.lock 文件。"""
    lock_path = _lock_path(project_root)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.debug(f"💾 repo.lock 已更新: {lock_path}")


def get_locked_commit(name: str, project_root: Path | None = None) -> str | None:
    """从 repo.lock 获取指定 repo 的锁定 commit（全量 SHA）。"""
    lock = load_lock(project_root)
    repos = lock.get("repos", {})
    info = repos.get(name, {})
    return info.get("commit")


def update_lock(
    name: str,
    commit: str,
    commit_short: str,
    branch: str,
    dirty: bool,
    project_root: Path | None = None,
) -> None:
    """更新 repo.lock 中指定 repo 的锁定信息。"""
    lock = load_lock(project_root)
    lock["generated_at"] = datetime.now().isoformat()
    repos = lock.setdefault("repos", {})
    repos[name] = {
        "commit": commit,
        "commit_short": commit_short,
        "pinned_at": datetime.now().isoformat(),
        "branch": branch,
        "dirty": dirty,
    }
    save_lock(lock, project_root)


def load_config(project_root: Path | None = None) -> dict:
    """加载 external/repos.yaml 配置。commit 为 None 表示 unpinned。"""
    import yaml

    cfg_path = _config_path(project_root)
    if not cfg_path.exists():
        logger.debug(f"外部配置不存在: {cfg_path}（使用 .env 默认路径）")
        return {}
    with open(cfg_path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    repos = data.get("repos", {})
    normalized = {}
    for name, info in repos.items():
        normalized[name] = {
            "path": info.get("path", f"external/{name}"),
            "branch": info.get("branch", "main"),
            "url": info.get("url", ""),
            "commit": info.get("commit"),
        }
    logger.debug(f"✅ 已加载外部配置: {cfg_path} | repos={list(normalized.keys())}")
    return normalized


def save_config(repos: dict, project_root: Path | None = None) -> Path:
    """保存 external/repos.yaml 配置。commit = None 表示 unpinned。"""
    import yaml

    cfg_path = _config_path(project_root)
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cfg_path, "w", encoding="utf-8") as f:
        yaml.dump({"repos": repos}, f, default_flow_style=False, allow_unicode=True)
    logger.info(f"💾 外部配置已保存: {cfg_path}")
    return cfg_path


def get_repos(
    project_root: Path | None = None,
    settings: Settings | None = None,
) -> list[ExternalRepo]:
    """获取所有外部 repo 配置（从 YAML 或 .env fallback）。"""
    cfg = load_config(project_root)
    if cfg:
        return [
            ExternalRepo(
                name=name,
                path=info["path"],
                branch=info["branch"],
                url=info.get("url", ""),
                commit=info.get("commit"),
            )
            for name, info in cfg.items()
        ]

    s = settings or get_settings()
    repos: list[ExternalRepo] = []
    if s.tradingagents_root:
        repos.append(
            ExternalRepo(
                name="tradingagents",
                path=str(
                    s.tradingagents_root.relative_to(s.project_root)
                    if str(s.tradingagents_root).startswith(str(s.project_root))
                    else s.tradingagents_root,
                ),
                branch="main",
                url="https://github.com/simonlin1212/TradingAgents-astock",
            ),
        )
    if s.kronos_root:
        repos.append(
            ExternalRepo(
                name="kronos",
                path=str(
                    s.kronos_root.relative_to(s.project_root)
                    if str(s.kronos_root).startswith(str(s.project_root))
                    else s.kronos_root,
                ),
                branch="main",
                url="https://github.com/shiyu-coder/Kronos",
            ),
        )
    return repos
