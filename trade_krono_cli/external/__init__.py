"""trade_krono_cli.external — 外部项目管理包。

拆分结构：
  external/models.py   — ExternalRepo / LockedRepo / RepoStatus 数据模型
  external/git_ops.py  — _git() / _get_git_status() / _is_pinned() Git 操作
  external/config_io.py — lock/config 文件 I/O 及 get_repos()
  external/__init__.py — 公共 API：status / doctor / update / pin / get_repro_info

所有符号统一从本包导出，保持向后兼容。
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from trade_krono_cli.external.config_io import (
    get_locked_commit,
    get_repos,
    load_config,
    load_lock,
    save_config,
    save_lock,
    update_lock,
)
from trade_krono_cli.external.git_ops import _get_git_status, _git, _is_pinned
from trade_krono_cli.external.models import ExternalRepo, LockedRepo, RepoStatus

__all__ = [
    "ExternalRepo",
    "LockedRepo",
    "RepoStatus",
    "_git",
    "_get_git_status",
    "_is_pinned",
    "load_lock",
    "save_lock",
    "get_locked_commit",
    "update_lock",
    "load_config",
    "save_config",
    "get_repos",
    "status",
    "doctor",
    "update",
    "pin",
    "get_repro_info",
]


def status(project_root: Path | None = None) -> list[RepoStatus]:
    """获取所有外部 repo 的运行时状态，对比 repo.lock 中的锁定版本。"""
    repos = get_repos(project_root)
    lock = load_lock(project_root)
    lock_repos = lock.get("repos", {})
    results: list[RepoStatus] = []
    for repo in repos:
        st = _get_git_status(repo.absolute_path, is_pinned=(repo.commit is not None))
        st.name = repo.name
        st.is_pinned = repo.commit is not None

        locked = lock_repos.get(repo.name, {})
        st.lock_commit = locked.get("commit")
        st.is_locked = bool(st.lock_commit)

        if st.commit and st.lock_commit and not st.commit.startswith(st.lock_commit):
            st.lock_mismatch = True
            st.error = f"lock 漂移：当前={st.commit_short} 期望(lock)={st.lock_commit[:12]}"

        results.append(st)
    return results


def doctor(project_root: Path | None = None) -> list[str]:
    """诊断外部 repo 状态，返回问题列表。空列表表示无问题。"""
    issues: list[str] = []
    for st in status(project_root):
        name = st.name
        if not st.path_exists:
            issues.append(f"[{name}] ❌ 路径不存在")
            continue
        if not st.is_git_repo:
            issues.append(f"[{name}] ⚠️  不是 git repo，无法追踪版本")
            continue
        if st.is_dirty:
            issues.append(f"[{name}] ⚠️  工作区有未提交修改（dirty），可能影响结果复现")
        if st.lock_mismatch:
            lock_short = st.lock_commit[:12] if st.lock_commit else "?"
            issues.append(
                f"[{name}] ❌ lock 漂移：repo.lock 锁定的是 {lock_short}，当前是 {st.commit_short}",
            )
        elif st.error and "未 pin" in st.error:
            issues.append(f"[{name}] ❌ {st.error}")
        if not st.is_locked and not st.is_pinned and st.is_up_to_date is False:
            issues.append(f"[{name}] ⚠️  本地分支落后于远程（可运行 repo update）")
    return issues


def update(project_root: Path | None = None) -> dict[str, str]:
    """拉取所有 unpinned repo 的最新代码，并自动刷新 repo.lock。"""
    repos = get_repos(project_root)
    results: dict[str, str] = {}
    for repo in repos:
        if repo.commit:
            results[repo.name] = f"⏭️  已 pinned（{repo.commit[:12]}），跳过 update"
            continue
        rc, out, err = _git(repo.absolute_path, "pull", "--ff-only")
        if rc == 0:
            _, commit, _ = _git(repo.absolute_path, "rev-parse", "HEAD")
            _, short, _ = _git(repo.absolute_path, "rev-parse", "--short", "HEAD")
            _, branch, _ = _git(repo.absolute_path, "branch", "--show-current")
            if commit:
                update_lock(repo.name, commit, short, branch or "main", False, project_root)
            results[repo.name] = f"✅ {out or '已是最新'}（lock 已刷新）"
        else:
            results[repo.name] = f"⚠️  {err or out or 'pull 失败'}"
    return results


def pin(
    name: str,
    commit: str,
    project_root: Path | None = None,
) -> bool:
    """将指定外部 repo pin 到指定 commit，同时更新 repos.yaml 和 repo.lock。"""
    target = next((r for r in get_repos(project_root) if r.name == name), None)
    if not target:
        msg = f"未知 repo: {name}（可用: {[r.name for r in get_repos(project_root)]}）"
        raise ValueError(msg)

    rc, full_sha, _ = _git(target.absolute_path, "rev-parse", "--verify", commit)
    if rc != 0:
        msg = f"commit 不存在: {commit}（在 {target.name} 中）"
        raise ValueError(msg)

    _, short, _ = _git(target.absolute_path, "rev-parse", "--short", full_sha)
    commit_short = short or full_sha[:12]

    cfg = load_config(project_root)
    if name not in cfg:
        cfg[name] = {
            "path": str(target.absolute_path.relative_to(Path(__file__).resolve().parent.parent)),
            "branch": target.branch,
            "url": target.url,
        }
    cfg[name]["commit"] = full_sha
    save_config(cfg, project_root)

    update_lock(name, full_sha, commit_short, target.branch, False, project_root)

    logger.info(f"📌 [{name}] 已 pin 到 {full_sha[:12]}（repos.yaml + repo.lock 已更新）")
    return True


def get_repro_info(project_root: Path | None = None) -> dict:
    """获取本次运行所需的外部 repo 复现信息（供 run snapshot 使用）。"""
    lock = load_lock(project_root)
    lock_repos = lock.get("repos", {})
    results: dict = {}
    for st in status(project_root):
        locked = lock_repos.get(st.name, {})
        results[st.name] = {
            "commit": st.commit_short or st.commit,
            "branch": st.branch,
            "pinned": st.is_pinned,
            "locked": st.is_locked,
            "dirty": st.is_dirty,
            "lock_commit": locked.get("commit_short") if locked else None,
            "lock_mismatch": st.lock_mismatch,
            "error": st.error,
        }
    return results
