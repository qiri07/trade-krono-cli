"""external/git_ops.py — 外部 repo 的 Git 操作工具函数。"""

from __future__ import annotations

import subprocess
from pathlib import Path

from trade_krono_cli.external.models import RepoStatus


def _git(repo_path: Path, *args: str) -> tuple[int, str, str]:
    """在指定目录执行 git 命令，返回 (returncode, stdout, stderr)。"""
    try:
        r = subprocess.run(
            ["git", "-C", str(repo_path), *args],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except FileNotFoundError:
        return 1, "", "git 未安装"
    except subprocess.TimeoutExpired:
        return 1, "", "git 命令超时"
    except Exception as e:
        return 1, "", str(e)


def _get_git_status(
    repo_path: Path,
    is_pinned: bool = False,
) -> RepoStatus:
    """获取单个 git repo 的状态。"""
    status = RepoStatus(name="", path_exists=repo_path.exists())
    if not status.path_exists:
        status.error = "路径不存在"
        return status

    status.is_git_repo = (repo_path / ".git").exists()
    if not status.is_git_repo:
        status.error = "不是 git repo（缺少 .git 目录）"
        return status

    rc, out, _ = _git(repo_path, "branch", "--show-current")
    status.branch = out if rc == 0 else None

    rc, out, _ = _git(repo_path, "rev-parse", "HEAD")
    status.commit = out if rc == 0 else None
    if status.commit:
        _, short, _ = _git(repo_path, "rev-parse", "--short", "HEAD")
        status.commit_short = short or status.commit[:8]

    rc, out, _ = _git(repo_path, "status", "--porcelain")
    status.is_dirty = bool(out.strip())

    rc, out, _ = _git(repo_path, "remote", "get-url", "origin")
    status.remote_url = out if rc == 0 else None

    if not is_pinned and status.branch and status.remote_url:
        _, ahead, _ = _git(repo_path, "rev-list", "--count", f"HEAD..origin/{status.branch}")
        _, behind, _ = _git(repo_path, "rev-list", "--count", f"origin/{status.branch}..HEAD")
        try:
            status.is_up_to_date = (int(ahead or 0) == 0) and (int(behind or 0) == 0)
        except ValueError:
            status.is_up_to_date = None

    return status


def _is_pinned(repo_path: Path, pinned_commit: str | None) -> bool:
    """检查 repo 是否已 pinned 到指定 commit。"""
    if not pinned_commit:
        return False
    rc, _out, _ = _git(repo_path, "rev-parse", "--verify", pinned_commit)
    return rc == 0
