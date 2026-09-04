"""external/models.py — 外部项目管理数据模型。

包含 ExternalRepo / LockedRepo / RepoStatus 三个 dataclass。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from trade_krono_cli.config import Settings, get_settings


@dataclass
class ExternalRepo:
    """单个外部 repo 的配置。"""

    name: str
    path: str
    branch: str
    url: str
    commit: str | None = None

    @property
    def absolute_path(self) -> Path:
        return get_settings().project_root / self.path

    def absolute_path_from(self, settings: Settings) -> Path:
        """Use provided settings instead of global singleton."""
        return settings.project_root / self.path


@dataclass
class LockedRepo:
    """repo.lock 中记录的锁定版本。"""

    commit: str
    commit_short: str
    pinned_at: str
    branch: str = "main"
    dirty: bool = False
    message: str = ""


@dataclass
class RepoStatus:
    """单个 repo 的运行时状态。"""

    name: str
    path_exists: bool
    is_git_repo: bool = False
    branch: str | None = None
    commit: str | None = None
    commit_short: str | None = None
    is_dirty: bool = False
    is_pinned: bool = False
    is_locked: bool = False
    is_up_to_date: bool | None = None
    remote_url: str | None = None
    error: str | None = None
    lock_commit: str | None = None
    lock_mismatch: bool = False
