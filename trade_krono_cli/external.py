"""
外部项目管理 — External Repo Manager。

管理依赖的下游项目（TradingAgents-astock、Kronos），支持：
  • repo status   — 查看各外部 repo 的分支/commit/dirty/up_to_date，对比 lock 文件
  • repo doctor   — 诊断问题（路径不存在、dirty、lock 漂移、branch mismatch）
  • repo update   — 拉取最新代码，自动刷新 repo.lock
  • repo pin      — 锁定到指定 commit，写入 repos.yaml + repo.lock

文件分工：
  external/repos.yaml   — 人类可编辑的配置（路径、分支、URL）
  external/repo.lock    — 机器维护的锁定版本（commit SHA + 时间戳）
                         每次 repo pin / repo update 后自动写入
                         .gitignore 推荐加入此文件以跟踪变更，但提交到 git 以确保复现
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger


# ═══════════════════════════════════════════════════════
# 数据模型
# ═══════════════════════════════════════════════════════

@dataclass
class ExternalRepo:
    """单个外部 repo 的配置。"""
    name: str
    path: str          # 相对项目根目录的路径
    branch: str        # 默认跟踪分支
    url: str           # 远程 git 地址
    commit: Optional[str] = None  # YAML 中指定的 commit（None = unpinned）

    @property
    def absolute_path(self) -> Path:
        from trade_krono_cli.config import get_settings
        return get_settings().project_root / self.path


@dataclass
class LockedRepo:
    """repo.lock 中记录的锁定版本。"""
    commit: str                    # 全量 SHA
    commit_short: str              # 前 12 位
    pinned_at: str                 # ISO 时间戳
    branch: str = "main"
    dirty: bool = False
    message: str = ""              # commit message（首行）


@dataclass
class RepoStatus:
    """单个 repo 的运行时状态。"""
    name: str
    path_exists: bool
    is_git_repo: bool = False
    branch: Optional[str] = None
    commit: Optional[str] = None
    commit_short: Optional[str] = None
    is_dirty: bool = False
    is_pinned: bool = False        # YAML 配置中是否 pinned
    is_locked: bool = False        # repo.lock 中是否有记录
    is_up_to_date: Optional[bool] = None
    remote_url: Optional[str] = None
    error: Optional[str] = None
    lock_commit: Optional[str] = None   # repo.lock 中记录的 commit
    lock_mismatch: bool = False      # 当前 commit 与 lock 不一致


# ═══════════════════════════════════════════════════════
# Git 工具函数
# ═══════════════════════════════════════════════════════

def _git(repo_path: Path, *args: str) -> tuple[int, str, str]:
    """在指定目录执行 git 命令，返回 (returncode, stdout, stderr)。"""
    try:
        r = subprocess.run(
            ["git", "-C", str(repo_path), *args],
            capture_output=True, text=True, timeout=10,
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

    # 当前 branch
    rc, out, _ = _git(repo_path, "branch", "--show-current")
    status.branch = out if rc == 0 else None

    # 当前 commit
    rc, out, _ = _git(repo_path, "rev-parse", "HEAD")
    status.commit = out if rc == 0 else None
    if status.commit:
        _, short, _ = _git(repo_path, "rev-parse", "--short", "HEAD")
        status.commit_short = short if short else status.commit[:8]

    # dirty 检查
    rc, out, _ = _git(repo_path, "status", "--porcelain")
    status.is_dirty = bool(out.strip())

    # remote URL
    rc, out, _ = _git(repo_path, "remote", "get-url", "origin")
    status.remote_url = out if rc == 0 else None

    # up_to_date（仅 unpinned 且有远程时有意义）
    if not is_pinned and status.branch and status.remote_url:
        _, ahead, _ = _git(repo_path, "rev-list", "--count", f"HEAD..origin/{status.branch}")
        _, behind, _ = _git(repo_path, "rev-list", "--count", f"origin/{status.branch}..HEAD")
        try:
            status.is_up_to_date = (int(ahead or 0) == 0) and (int(behind or 0) == 0)
        except ValueError:
            status.is_up_to_date = None

    return status


# ═══════════════════════════════════════════════════════
# Lock 文件读写（repo.lock）
# ═══════════════════════════════════════════════════════

_LOCK_FILENAME = "repo.lock"


def _lock_path(project_root: Optional[Path] = None) -> Path:
    root = project_root or Path(__file__).resolve().parent.parent
    return root / "external" / _LOCK_FILENAME


def load_lock(project_root: Optional[Path] = None) -> dict:
    """
    加载 repo.lock 文件。

    Returns
    -------
    dict : {"generated_at": str, "repos": {name: LockedRepo 字段}}
      文件不存在时返回空 dict
    """
    lock_path = _lock_path(project_root)
    if not lock_path.exists():
        return {}
    try:
        with open(lock_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"⚠️  repo.lock 读取失败: {e}")
        return {}


def save_lock(data: dict, project_root: Optional[Path] = None) -> None:
    """
    保存 repo.lock 文件。

    Parameters
    ----------
    data : dict
      {"generated_at": ISO timestamp, "repos": {name: {...}}}
    """
    lock_path = _lock_path(project_root)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.debug(f"💾 repo.lock 已更新: {lock_path}")


def get_locked_commit(name: str, project_root: Optional[Path] = None) -> Optional[str]:
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
    project_root: Optional[Path] = None,
) -> None:
    """
    更新 repo.lock 中指定 repo 的锁定信息。

    Parameters
    ----------
    name         : repo 名称
    commit       : 全量 commit SHA
    commit_short : 短 SHA（前 12 位）
    branch       : 当前分支
    dirty        : 工作区是否 dirty
    """
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


def _is_pinned(repo_path: Path, pinned_commit: Optional[str]) -> bool:
    """检查 repo 是否已 pinned 到指定 commit。"""
    if not pinned_commit:
        return False
    rc, out, _ = _git(repo_path, "rev-parse", "--verify", pinned_commit)
    return rc == 0


# ═══════════════════════════════════════════════════════
# 配置文件读写
# ═══════════════════════════════════════════════════════

_CONFIG_FILENAME = "repos.yaml"


def _config_path(project_root: Optional[Path] = None) -> Path:
    """获取配置文件路径。"""
    root = project_root or Path(__file__).resolve().parent.parent
    return root / "external" / _CONFIG_FILENAME


def load_config(
    project_root: Optional[Path] = None,
) -> dict:
    """
    加载 external/repos.yaml 配置。

    Returns
    -------
    dict : {name: {path, branch, url, commit}}
      commit 为 None 表示 unpinned（跟踪 branch）
    """
    import yaml
    cfg_path = _config_path(project_root)
    if not cfg_path.exists():
        logger.debug(f"外部配置不存在: {cfg_path}（使用 .env 默认路径）")
        return {}
    with open(cfg_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    repos = data.get("repos", {})
    # 规范化：确保 commit 字段存在
    normalized = {}
    for name, info in repos.items():
        normalized[name] = {
            "path": info.get("path", f"external/{name}"),
            "branch": info.get("branch", "main"),
            "url": info.get("url", ""),
            "commit": info.get("commit"),  # None = unpinned
        }
    logger.debug(f"✅ 已加载外部配置: {cfg_path} | repos={list(normalized.keys())}")
    return normalized


def save_config(
    repos: dict,
    project_root: Optional[Path] = None,
) -> Path:
    """
    保存 external/repos.yaml 配置。

    Parameters
    ----------
    repos : dict
      {name: {path, branch, url, commit}}
      commit = None 表示 unpinned
    """
    import yaml
    root = project_root or Path(__file__).resolve().parent.parent
    cfg_path = root / "external" / _CONFIG_FILENAME
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cfg_path, "w", encoding="utf-8") as f:
        yaml.dump({"repos": repos}, f, default_flow_style=False, allow_unicode=True)
    logger.info(f"💾 外部配置已保存: {cfg_path}")
    return cfg_path


# ═══════════════════════════════════════════════════════
# 核心 API
# ═══════════════════════════════════════════════════════

def get_repos(project_root: Optional[Path] = None) -> list[ExternalRepo]:
    """
    获取所有外部 repo 配置（从 YAML 或 .env fallback）。

    优先级：
      1. external/repos.yaml
      2. .env 中的 TRADINGAGENTS_ROOT / KRONOS_ROOT
    """
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

    # fallback: 从 .env 构造默认配置
    from trade_krono_cli.config import get_settings
    s = get_settings()
    repos = []
    if s.tradingagents_root:
        repos.append(ExternalRepo(
            name="tradingagents",
            path=str(s.tradingagents_root.relative_to(s.project_root)
                     if str(s.tradingagents_root).startswith(str(s.project_root))
                     else s.tradingagents_root),
            branch="main",
            url="https://github.com/simonlin1212/TradingAgents-astock",
        ))
    if s.kronos_root:
        repos.append(ExternalRepo(
            name="kronos",
            path=str(s.kronos_root.relative_to(s.project_root)
                     if str(s.kronos_root).startswith(str(s.project_root))
                     else s.kronos_root),
            branch="main",
            url="https://github.com/shiyu-coder/Kronos",
        ))
    return repos


def status(project_root: Optional[Path] = None) -> list[RepoStatus]:
    """
    获取所有外部 repo 的运行时状态，对比 repo.lock 中的锁定版本。

    Returns
    -------
    list[RepoStatus]
    """
    repos = get_repos(project_root)
    lock = load_lock(project_root)
    lock_repos = lock.get("repos", {})
    results = []
    for repo in repos:
        st = _get_git_status(repo.absolute_path, is_pinned=(repo.commit is not None))
        st.name = repo.name
        st.is_pinned = repo.commit is not None

        # 检查 repo.lock 中的记录
        locked = lock_repos.get(repo.name, {})
        st.lock_commit = locked.get("commit")
        st.is_locked = bool(st.lock_commit)

        # 对比当前 commit 与 lock 文件
        if st.commit and st.lock_commit:
            if not st.commit.startswith(st.lock_commit):
                st.lock_mismatch = True
                st.error = (
                    f"lock 漂移：当前={st.commit_short} "
                    f"期望(lock)={st.lock_commit[:12]}"
                )

        results.append(st)
    return results


def doctor(project_root: Optional[Path] = None) -> list[str]:
    """
    诊断外部 repo 状态，返回问题列表。

    检查项：
      • 路径是否存在
      • 是否为 git repo
      • 是否 dirty
      • repo.lock 与当前 commit 是否一致（lock 漂移）
      • YAML pinned commit 是否与当前一致
      • unpinned 时是否落后于远程

    Returns
    -------
    list[str] : 每条是一个问题描述，空列表表示无问题
    """
    issues = []
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
            issues.append(f"[{name}] ❌ lock 漂移：repo.lock 锁定的是 {lock_short}，"
                          f"当前是 {st.commit_short}")
        elif st.error and "未 pin" in st.error:
            issues.append(f"[{name}] ❌ {st.error}")
        if not st.is_locked and not st.is_pinned and st.is_up_to_date is False:
            issues.append(f"[{name}] ⚠️  本地分支落后于远程（可运行 repo update）")
    return issues


def update(project_root: Optional[Path] = None) -> dict[str, str]:
    """
    拉取所有 unpinned repo 的最新代码，并自动刷新 repo.lock。

    Parameters
    ----------
    project_root : 项目根目录（可选）

    Returns
    -------
    dict : {name: result_msg}
    """
    repos = get_repos(project_root)
    results: dict[str, str] = {}
    for repo in repos:
        if repo.commit:
            results[repo.name] = f"⏭️  已 pinned（{repo.commit[:12]}），跳过 update"
            continue
        rc, out, err = _git(repo.absolute_path, "pull", "--ff-only")
        if rc == 0:
            # 拉取成功后刷新 lock 文件
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
    project_root: Optional[Path] = None,
) -> bool:
    """
    将指定外部 repo pin 到指定 commit，同时更新 repos.yaml 和 repo.lock。

    Parameters
    ----------
    name   : repo 名称（tradingagents / kronos）
    commit : commit SHA（长或短均可）
    project_root : 项目根目录（可选）

    Returns
    -------
    bool : 成功返回 True
    """
    repos = get_repos(project_root)
    target = next((r for r in repos if r.name == name), None)
    if not target:
        raise ValueError(f"未知 repo: {name}（可用: {[r.name for r in repos]}）")

    # 验证 commit 存在并获取全量 SHA
    rc, full_sha, _ = _git(target.absolute_path, "rev-parse", "--verify", commit)
    if rc != 0:
        raise ValueError(f"commit 不存在: {commit}（在 {target.name} 中）")

    _, short, _ = _git(target.absolute_path, "rev-parse", "--short", full_sha)
    commit_short = short or full_sha[:12]

    # 更新 YAML 配置
    cfg = load_config(project_root)
    if name not in cfg:
        cfg[name] = {
            "path": str(target.absolute_path.relative_to(
                Path(__file__).resolve().parent.parent
            )),
            "branch": target.branch,
            "url": target.url,
        }
    cfg[name]["commit"] = full_sha
    save_config(cfg, project_root)

    # 同步更新 repo.lock
    update_lock(name, full_sha, commit_short, target.branch, False, project_root)

    logger.info(f"📌 [{name}] 已 pin 到 {full_sha[:12]}（repos.yaml + repo.lock 已更新）")
    return True


def get_repro_info(project_root: Optional[Path] = None) -> dict:
    """
    获取本次运行所需的外部 repo 复现信息（供 run snapshot 使用）。

    优先级：repo.lock > 当前 git HEAD

    Returns
    -------
    dict : {name: {commit, branch, pinned, locked, dirty}}
    """
    lock = load_lock(project_root)
    lock_repos = lock.get("repos", {})
    results = {}
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
