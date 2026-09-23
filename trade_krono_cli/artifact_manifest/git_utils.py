"""artifact_manifest.git_utils — Git 状态检测工具。"""

from __future__ import annotations

import subprocess
from pathlib import Path

from loguru import logger


def _git_sha(repo_path: Path) -> tuple[str | None, str | None]:
    """返回 (full_sha, short_sha)；路径不存在或非 git repo 时返回 (None, None)。"""
    if not (repo_path / ".git").exists():
        return None, None
    try:
        result_full = subprocess.run(
            ["git", "-C", str(repo_path), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result_full.returncode != 0:
            return None, None
        full = result_full.stdout.strip()

        result_short = subprocess.run(
            ["git", "-C", str(repo_path), "rev-parse", "--short=12", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        short = result_short.stdout.strip() if result_short.returncode == 0 else full[:12]
        return full, short
    except Exception as e:
        logger.debug(f"git commit hash 检测失败: {e}")
    return None, None


def _git_dirty(repo_path: Path) -> bool:
    """判断 git 仓库是否有未提交的修改。"""
    if not (repo_path / ".git").exists():
        return False
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        out = result.stdout if result.returncode == 0 else ""
        return bool(out.strip())
    except Exception as e:
        logger.debug(f"git 脏检测失败: {e}")
        return False

