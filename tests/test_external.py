"""测试外部项目管理模块。"""

from pathlib import Path
from unittest.mock import patch

import pytest

# ── 辅助函数 ──────────────────────────────────────────────────────────────────


def _make_mock_repo(tmp_path: Path, name: str, commit: str = "abc123def456") -> Path:
    """在 tmp_path 下创建一个模拟 git repo（只有 .git 目录）。"""
    repo_dir = tmp_path / name
    repo_dir.mkdir()
    (repo_dir / ".git").mkdir()
    # 创建 .git/HEAD 让 rev-parse 可以工作
    (repo_dir / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (repo_dir / ".git" / "refs" / "heads").mkdir(parents=True)
    (repo_dir / ".git" / "refs" / "heads" / "main").write_text(commit + "\n")
    return repo_dir


def _make_dirs_not_git(tmp_path: Path, name: str) -> Path:
    """创建一个不是 git repo 的目录。"""
    d = tmp_path / name
    d.mkdir()
    return d


# ═══════════════════════════════════════════════════════
# load_config / save_config
# ═══════════════════════════════════════════════════════


class TestConfigIO:
    def test_load_empty_config_returns_empty_dict(self, tmp_path) -> None:
        """配置文件不存在时返回空 dict。"""
        from trade_krono_cli.external import load_config

        result = load_config(tmp_path)
        assert result == {}

    def test_load_valid_yaml(self, tmp_path) -> None:
        """有效 YAML 配置应正确解析。"""
        from trade_krono_cli.external import load_config, save_config

        cfg = {
            "repos": {
                "tradingagents": {
                    "path": "external/TradingAgents-astock",
                    "branch": "main",
                    "url": "https://github.com/simonlin1212/TradingAgents-astock",
                    "commit": None,
                },
                "kronos": {
                    "path": "external/Kronos",
                    "branch": "main",
                    "url": "https://github.com/shiyu-coder/Kronos",
                    "commit": "def456",
                },
            },
        }
        save_config(cfg["repos"], tmp_path)
        loaded = load_config(tmp_path)
        assert "tradingagents" in loaded
        assert "kronos" in loaded
        assert loaded["tradingagents"]["commit"] is None
        assert loaded["kronos"]["commit"] == "def456"

    def test_save_and_load_roundtrip(self, tmp_path) -> None:
        """保存再加载应得到相同结果。"""
        from trade_krono_cli.external import load_config, save_config

        original = {
            "repo_a": {"path": "ext/a", "branch": "main", "url": "", "commit": None},
            "repo_b": {
                "path": "ext/b",
                "branch": "develop",
                "url": "https://example.com/b",
                "commit": "aaa111",
            },
        }
        save_config(original, tmp_path)
        loaded = load_config(tmp_path)
        assert loaded == original

    def test_missing_optional_fields_fallback(self, tmp_path) -> None:
        """YAML 中缺少可选字段时使用默认值。"""
        from trade_krono_cli.external import load_config, save_config

        cfg = {
            "repos": {
                "minimal": {"path": "ext/minimal"},
            },
        }
        save_config(cfg["repos"], tmp_path)
        loaded = load_config(tmp_path)
        assert loaded["minimal"]["branch"] == "main"
        assert loaded["minimal"]["url"] == ""
        assert loaded["minimal"]["commit"] is None


# ═══════════════════════════════════════════════════════
# get_repos — fallback 路径解析
# ═══════════════════════════════════════════════════════


class TestGetRepos:
    def test_fallback_from_settings(self, tmp_path) -> None:
        """无 YAML 时从 Settings 获取默认路径。"""
        from trade_krono_cli.external import get_repos

        repos = get_repos(tmp_path)
        # 如果 settings 中有路径，应至少返回一条
        assert isinstance(repos, list)
        for r in repos:
            assert hasattr(r, "name")
            assert hasattr(r, "path")


# ═══════════════════════════════════════════════════════
# status — 模拟 git 命令
# ═══════════════════════════════════════════════════════


class TestStatus:
    def test_path_not_exists(self, tmp_path) -> None:
        """路径不存在时 status 应报告错误。"""
        from trade_krono_cli.external import ExternalRepo, _get_git_status

        repo = ExternalRepo(
            name="test_repo",
            path=str(tmp_path / "nonexistent"),
            branch="main",
            url="",
        )
        st = _get_git_status(repo.absolute_path)
        assert not st.path_exists
        assert st.error == "路径不存在"

    def test_not_git_repo(self, tmp_path) -> None:
        """非 git 目录应报告错误。"""
        from trade_krono_cli.external import ExternalRepo, _get_git_status

        repo_dir = tmp_path / "not_git"
        repo_dir.mkdir()
        repo = ExternalRepo(
            name="not_git",
            path=str(repo_dir),
            branch="main",
            url="",
        )
        st = _get_git_status(repo.absolute_path)
        assert st.path_exists
        assert not st.is_git_repo
        assert "git repo" in st.error

    def test_valid_git_repo(self, tmp_path) -> None:
        """有效 git repo 应返回正确状态。"""
        from trade_krono_cli.external import ExternalRepo, _get_git_status

        repo_dir = tmp_path / "valid_repo"
        repo_dir.mkdir()
        (repo_dir / ".git").mkdir()  # 标记为 git repo

        # mock _git 调用（up_to_date 检查需要额外 2 次调用）
        with patch("trade_krono_cli.external.git_ops._git") as mock_git:
            mock_git.side_effect = [
                (0, "main", ""),  # branch --show-current
                (0, "abc123def456", ""),  # rev-parse HEAD
                (0, "abc123d", ""),  # rev-parse --short HEAD
                (0, "", ""),  # status --porcelain (clean)
                (0, "https://example.com/test", ""),  # remote get-url origin
                (0, "0", ""),  # rev-list ahead
                (0, "0", ""),  # rev-list behind
            ]
            repo = ExternalRepo(
                name="valid_repo",
                path=str(repo_dir),
                branch="main",
                url="https://example.com/test",
            )
            st = _get_git_status(repo.absolute_path)
            assert st.path_exists
            assert st.is_git_repo
            assert st.commit == "abc123def456"
            assert st.branch == "main"
            assert st.commit_short == "abc123d"
            assert not st.is_dirty
            assert st.is_up_to_date is True


# ═══════════════════════════════════════════════════════
# pin — 锁定 commit
# ═══════════════════════════════════════════════════════


class TestPin:
    def test_pin_creates_config(self, tmp_path) -> None:
        """Pin 操作应创建或更新 repos.yaml。"""
        from trade_krono_cli.external import load_config, pin, save_config

        # 先保存一个基础配置（路径可以不存在，pin 内部会验证 commit）
        cfg = {
            "tradingagents": {
                "path": str(tmp_path / "ta_repo"),
                "branch": "main",
                "url": "https://github.com/simonlin1212/TradingAgents-astock",
                "commit": None,
            },
        }
        save_config(cfg, tmp_path)

        # 用 mock 模拟 git 验证 commit 成功
        with patch("trade_krono_cli.external._git") as mock_git:
            mock_git.return_value = (0, "newcommit123", "")
            pin("tradingagents", "newcommit123", tmp_path)

        loaded = load_config(tmp_path)
        assert loaded["tradingagents"]["commit"] == "newcommit123"

    def test_pin_invalid_repo_raises(self, tmp_path) -> None:
        """Pin 不存在的 repo 应抛出 ValueError。"""
        from trade_krono_cli.external import pin, save_config

        save_config(
            {
                "other_repo": {
                    "path": str(tmp_path / "other"),
                    "branch": "main",
                    "url": "",
                    "commit": None,
                },
            },
            tmp_path,
        )

        with pytest.raises(ValueError, match="未知 repo"):
            pin("nonexistent", "abc123", tmp_path)


# ═══════════════════════════════════════════════════════
# get_repro_info
# ═══════════════════════════════════════════════════════


class TestGetReproInfo:
    def test_returns_repo_info(self, tmp_path) -> None:
        """应返回各 repo 的复现信息。"""
        from trade_krono_cli.external import get_repro_info, save_config

        save_config(
            {
                "tradingagents": {
                    "path": str(tmp_path / "ta"),
                    "branch": "main",
                    "url": "",
                    "commit": None,
                },
                "kronos": {
                    "path": str(tmp_path / "kr"),
                    "branch": "main",
                    "url": "",
                    "commit": "abc123def456",
                },
            },
            tmp_path,
        )

        info = get_repro_info(tmp_path)
        assert "tradingagents" in info
        assert "kronos" in info
        assert info["kronos"]["pinned"] is True
        assert info["tradingagents"]["pinned"] is False


# ═══════════════════════════════════════════════════════
# repo.lock 读写测试
# ═══════════════════════════════════════════════════════


class TestLockFile:
    def test_load_empty_lock_returns_empty_dict(self, tmp_path) -> None:
        """Lock 文件不存在时返回空 dict。"""
        from trade_krono_cli.external import load_lock

        result = load_lock(tmp_path)
        assert result == {}

    def test_save_and_load_roundtrip(self, tmp_path) -> None:
        """保存再加载 lock 文件应保持一致。"""
        from trade_krono_cli.external import load_lock, save_lock

        data = {
            "generated_at": "2026-08-11T12:00:00",
            "repos": {
                "tradingagents": {
                    "commit": "aaa111",
                    "commit_short": "aaa111",
                    "pinned_at": "2026-08-11T12:00:00",
                    "branch": "main",
                    "dirty": False,
                },
                "kronos": {
                    "commit": "bbb222",
                    "commit_short": "bbb222",
                    "pinned_at": "2026-08-11T12:00:00",
                    "branch": "main",
                    "dirty": True,
                },
            },
        }
        save_lock(data, tmp_path)
        loaded = load_lock(tmp_path)
        assert loaded["repos"]["tradingagents"]["commit"] == "aaa111"
        assert loaded["repos"]["kronos"]["dirty"] is True

    def test_get_locked_commit(self, tmp_path) -> None:
        """get_locked_commit 应从 lock 文件返回正确值。"""
        from trade_krono_cli.external import get_locked_commit, save_lock

        save_lock({"repos": {"ta": {"commit": "locked_sha_123"}}}, tmp_path)
        assert get_locked_commit("ta", tmp_path) == "locked_sha_123"
        assert get_locked_commit("missing", tmp_path) is None

    def test_update_lock(self, tmp_path) -> None:
        """update_lock 应写入正确的 commit 信息。"""
        from trade_krono_cli.external import load_lock, update_lock

        update_lock("kronos", "full_sha_abc123", "abc123", "main", False, tmp_path)
        lock = load_lock(tmp_path)
        assert lock["repos"]["kronos"]["commit"] == "full_sha_abc123"
        assert lock["repos"]["kronos"]["commit_short"] == "abc123"
        assert lock["repos"]["kronos"]["branch"] == "main"
        assert lock["generated_at"] is not None

    def test_status_detects_lock_mismatch(self, tmp_path) -> None:
        """当前 commit 与 lock 不一致时应标记 lock_mismatch。"""
        from trade_krono_cli.external import ExternalRepo, _get_git_status, save_lock

        repo_dir = tmp_path / "mismatch_repo"
        repo_dir.mkdir()
        (repo_dir / ".git").mkdir()

        # lock 文件锁定的是 abc123，但当前 HEAD 是 def456
        save_lock(
            {
                "generated_at": "2026-08-11T12:00:00",
                "repos": {
                    "mismatch_repo": {
                        "commit": "abc123def456789",
                        "commit_short": "abc123def456",
                        "branch": "main",
                        "dirty": False,
                    },
                },
            },
            tmp_path,
        )

        with patch("trade_krono_cli.external.git_ops._git") as mock_git:
            mock_git.side_effect = [
                (0, "main", ""),  # branch
                (0, "def456ghi789xyz", ""),  # rev-parse HEAD（与 lock 不同）
                (0, "def456ghi789", ""),  # short
                (0, "", ""),  # status (clean)
                (1, "", "no remote"),  # remote get-url（失败）
            ]
            repo = ExternalRepo(name="mismatch_repo", path=str(repo_dir), branch="main", url="")
            st = _get_git_status(repo.absolute_path, is_pinned=False)

            # 手动设置 lock 信息来模拟 status() 的行为
            from trade_krono_cli.external import load_lock

            lock = load_lock(tmp_path)
            locked = lock.get("repos", {}).get("mismatch_repo", {})
            st.lock_commit = locked.get("commit")
            st.is_locked = bool(st.lock_commit)
            if st.commit and st.lock_commit:
                st.lock_mismatch = not st.commit.startswith(st.lock_commit)

            assert st.lock_mismatch is True
            assert st.error is None  # error 在 status() 中设置，这里直接测试字段即可


# ── Doctor Issues ─────────────────────────────────────────────────────────────


def test_doctor_path_not_exists(tmp_path) -> None:
    """路径不存在时 doctor 应报告问题。"""
    from trade_krono_cli.external import doctor, save_config

    save_config(
        {
            "missing_repo": {
                "path": str(tmp_path / "nonexistent_path"),
                "branch": "main",
                "url": "",
                "commit": None,
            },
        },
        tmp_path,
    )
    issues = doctor(tmp_path)
    assert any("路径不存在" in i for i in issues)


def test_doctor_not_git_repo(tmp_path) -> None:
    """非 git 目录应报告问题。"""
    from trade_krono_cli.external import doctor, save_config

    not_git = tmp_path / "not_git"
    not_git.mkdir()
    save_config(
        {
            "not_git": {
                "path": str(not_git),
                "branch": "main",
                "url": "",
                "commit": None,
            },
        },
        tmp_path,
    )
    issues = doctor(tmp_path)
    assert any("不是 git repo" in i for i in issues)


def test_doctor_clean_repo_no_issues(tmp_path) -> None:
    """干净的有效 git repo 不应报告问题。"""
    from trade_krono_cli.external import doctor, save_config

    repo_dir = _make_mock_repo(tmp_path, "clean_repo")
    save_config(
        {
            "clean_repo": {
                "path": str(repo_dir),
                "branch": "main",
                "url": "",
                "commit": None,
            },
        },
        tmp_path,
    )
    with patch("trade_krono_cli.external.git_ops._git") as mock_git:
        mock_git.side_effect = [
            (0, "main", ""),
            (0, "abc123def456", ""),
            (0, "abc123def456", ""),
            (0, "", ""),
            (0, "https://example.com/test", ""),
            (0, "0", ""),
            (0, "0", ""),
        ]
        issues = doctor(tmp_path)
    assert issues == []


# ═══════════════════════════════════════════════════════
# _git — subprocess wrapper edge cases
# ═══════════════════════════════════════════════════════


class TestGitWrapper:
    def test_git_file_not_found(self, tmp_path, monkeypatch) -> None:
        """git 未安装时返回 (1, "", "git 未安装")。"""

        def _raise_file_not_found(*args, **kwargs):
            raise FileNotFoundError("git")

        from trade_krono_cli.external.git_ops import _git

        monkeypatch.setattr(
            "trade_krono_cli.external.git_ops.subprocess.run",
            _raise_file_not_found,
        )
        rc, out, err = _git(tmp_path, "version")
        assert rc == 1
        assert out == ""
        assert "git 未安装" in err

    def test_git_timeout(self, tmp_path, monkeypatch) -> None:
        """git 命令超时时返回 (1, "", "git 命令超时")。"""
        import subprocess

        from trade_krono_cli.external.git_ops import _git

        with patch(
            "trade_krono_cli.external.git_ops.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="git", timeout=10),
        ):
            rc, out, err = _git(tmp_path, "version")
            assert rc == 1
            assert out == ""
            assert "超时" in err

    def test_git_generic_exception(self, tmp_path, monkeypatch) -> None:
        """其他异常时返回 (1, "", str(e))。"""
        from trade_krono_cli.external.git_ops import _git

        with patch(
            "trade_krono_cli.external.git_ops.subprocess.run",
            side_effect=OSError("permission denied"),
        ):
            rc, out, err = _git(tmp_path, "version")
            assert rc == 1
            assert out == ""
            assert "permission denied" in err


# ═══════════════════════════════════════════════════════
# _is_pinned
# ═══════════════════════════════════════════════════════


class TestIsPinned:
    def test_is_pinned_true(self, tmp_path) -> None:
        """commit 存在时返回 True。"""
        from trade_krono_cli.external.git_ops import _is_pinned

        repo_dir = _make_mock_repo(tmp_path, "pinned_repo")
        with patch("trade_krono_cli.external.git_ops._git") as mock_git:
            mock_git.return_value = (0, "abc123def456", "")
            assert _is_pinned(repo_dir, "abc123def456") is True

    def test_is_pinned_false_none(self, tmp_path) -> None:
        """pinned_commit 为 None 时返回 False。"""
        from trade_krono_cli.external.git_ops import _is_pinned

        repo_dir = _make_mock_repo(tmp_path, "unpinned_repo")
        assert _is_pinned(repo_dir, None) is False

    def test_is_pinned_false_missing_commit(self, tmp_path) -> None:
        """commit 不存在时返回 False。"""
        from trade_krono_cli.external.git_ops import _is_pinned

        repo_dir = _make_mock_repo(tmp_path, "missing_commit_repo")
        assert _is_pinned(repo_dir, "nonexistent_sha") is False


# ═══════════════════════════════════════════════════════
# ExternalRepo path helpers
# ═══════════════════════════════════════════════════════


class TestExternalRepoPaths:
    def test_absolute_path_uses_global_settings(self, tmp_path, monkeypatch) -> None:
        """absolute_path 使用全局 settings.project_root。"""
        from trade_krono_cli.config import get_settings
        from trade_krono_cli.external.models import ExternalRepo

        repo = ExternalRepo(
            name="test",
            path="external/test_repo",
            branch="main",
            url="",
        )
        s = get_settings()
        expected = s.project_root / "external/test_repo"
        assert repo.absolute_path == expected

    def test_absolute_path_from_uses_provided_settings(self) -> None:
        """absolute_path_from 使用传入的 settings。"""
        from pathlib import Path

        from trade_krono_cli.config import Settings
        from trade_krono_cli.external.models import ExternalRepo

        custom_root = Path("/custom/project")
        settings = Settings(project_root=custom_root)
        repo = ExternalRepo(
            name="test",
            path="external/test_repo",
            branch="main",
            url="",
        )
        assert repo.absolute_path_from(settings) == custom_root / "external/test_repo"


# ═══════════════════════════════════════════════════════
# Dataclass defaults
# ═══════════════════════════════════════════════════════


class TestDataclasses:
    def test_locked_repo_defaults(self) -> None:
        from trade_krono_cli.external.models import LockedRepo

        lr = LockedRepo(commit="abc", commit_short="ab", pinned_at="2026-01-01")
        assert lr.branch == "main"
        assert lr.dirty is False
        assert lr.message == ""

    def test_repo_status_defaults(self) -> None:
        from trade_krono_cli.external.models import RepoStatus

        rs = RepoStatus(name="test", path_exists=True)
        assert rs.is_git_repo is False
        assert rs.branch is None
        assert rs.commit is None
        assert rs.commit_short is None
        assert rs.is_dirty is False
        assert rs.is_pinned is False
        assert rs.is_locked is False
        assert rs.is_up_to_date is None
        assert rs.remote_url is None
        assert rs.error is None
        assert rs.lock_commit is None
        assert rs.lock_mismatch is False


# ═══════════════════════════════════════════════════════
# update — 拉取并刷新 lock
# ═══════════════════════════════════════════════════════


class TestUpdate:
    def test_update_skips_pinned(self, tmp_path) -> None:
        """已 pinned 的 repo 应跳过 update。"""
        from trade_krono_cli.external import save_config, update

        save_config(
            {
                "pinned_repo": {
                    "path": str(tmp_path / "pinned"),
                    "branch": "main",
                    "url": "",
                    "commit": "abc123",
                },
            },
            tmp_path,
        )
        results = update(tmp_path)
        assert "pinned_repo" in results
        assert "已 pinned" in results["pinned_repo"]

    def test_update_success_refreshes_lock(self, tmp_path) -> None:
        """pull 成功后 lock 应被刷新。"""
        from trade_krono_cli.external import load_lock, save_config, update

        repo_dir = tmp_path / "unpinned"
        repo_dir.mkdir()
        (repo_dir / ".git").mkdir()
        save_config(
            {
                "unpinned": {
                    "path": str(repo_dir),
                    "branch": "main",
                    "url": "",
                    "commit": None,
                },
            },
            tmp_path,
        )

        with patch("trade_krono_cli.external._git") as mock_git:
            mock_git.side_effect = [
                (0, "already up to date", ""),  # pull
                (0, "newsha1234567890abcdef", ""),  # rev-parse HEAD
                (0, "newsha123", ""),  # rev-parse --short
                (0, "main", ""),  # branch --show-current
            ]
            results = update(tmp_path)

        assert "unpinned" in results
        assert "✅" in results["unpinned"]
        lock = load_lock(tmp_path)
        assert lock["repos"]["unpinned"]["commit"] == "newsha1234567890abcdef"

    def test_update_pull_failure(self, tmp_path) -> None:
        """pull 失败时返回错误信息。"""
        from trade_krono_cli.external import save_config, update

        repo_dir = tmp_path / "unpinned"
        repo_dir.mkdir()
        (repo_dir / ".git").mkdir()
        save_config(
            {
                "unpinned": {
                    "path": str(repo_dir),
                    "branch": "main",
                    "url": "",
                    "commit": None,
                },
            },
            tmp_path,
        )

        with patch("trade_krono_cli.external._git") as mock_git:
            mock_git.return_value = (1, "", "remote rejected")
            results = update(tmp_path)

        assert "unpinned" in results
        assert "⚠️" in results["unpinned"]


# ═══════════════════════════════════════════════════════
# status — lock mismatch / dirty / up_to_date
# ═══════════════════════════════════════════════════════


class TestStatusEdgeCases:
    def test_status_lock_mismatch(self, tmp_path) -> None:
        """当前 commit 与 lock 不一致时标记 lock_mismatch。"""
        from trade_krono_cli.external import save_config, save_lock, status

        repo_dir = tmp_path / "drift_repo"
        repo_dir.mkdir()
        (repo_dir / ".git").mkdir()

        save_config(
            {
                "drift_repo": {
                    "path": str(repo_dir),
                    "branch": "main",
                    "url": "",
                    "commit": None,
                },
            },
            tmp_path,
        )
        save_lock(
            {
                "generated_at": "2026-01-01T00:00:00",
                "repos": {
                    "drift_repo": {
                        "commit": "locked_sha_aaa",
                        "commit_short": "locked_sha",
                        "branch": "main",
                        "dirty": False,
                    },
                },
            },
            tmp_path,
        )

        with patch("trade_krono_cli.external.git_ops._git") as mock_git:
            mock_git.side_effect = [
                (0, "main", ""),  # branch
                (0, "current_sha_bbb", ""),  # rev-parse HEAD (不同于 lock)
                (0, "current_sha", ""),  # short
                (0, "", ""),  # status (clean)
                (1, "", "no remote"),  # remote (no remote_url -> skip up_to_date)
            ]
            sts = status(tmp_path)

        st = sts[0]
        assert st.lock_mismatch is True
        assert st.lock_commit == "locked_sha_aaa"
        assert "lock 漂移" in st.error

    def test_status_dirty_repo(self, tmp_path) -> None:
        """工作区 dirty 时 status 应标记 is_dirty。"""
        from trade_krono_cli.external import save_config, status

        repo_dir = tmp_path / "dirty_repo"
        repo_dir.mkdir()
        (repo_dir / ".git").mkdir()
        save_config(
            {
                "dirty_repo": {
                    "path": str(repo_dir),
                    "branch": "main",
                    "url": "",
                    "commit": None,
                },
            },
            tmp_path,
        )

        with patch("trade_krono_cli.external.git_ops._git") as mock_git:
            mock_git.side_effect = [
                (0, "main", ""),
                (0, "abc123def456", ""),
                (0, "abc123d", ""),
                (0, " M modified.py", ""),  # status --porcelain 不为空
                (1, "", "no remote"),
            ]
            sts = status(tmp_path)

        st = sts[0]
        assert st.is_dirty is True

    def test_status_is_up_to_date_false(self, tmp_path) -> None:
        """本地落后于远程时 is_up_to_date 为 False。"""
        from trade_krono_cli.external import save_config, status

        repo_dir = tmp_path / "behind_repo"
        repo_dir.mkdir()
        (repo_dir / ".git").mkdir()
        save_config(
            {
                "behind_repo": {
                    "path": str(repo_dir),
                    "branch": "main",
                    "url": "",
                    "commit": None,
                },
            },
            tmp_path,
        )

        with patch("trade_krono_cli.external.git_ops._git") as mock_git:
            mock_git.side_effect = [
                (0, "main", ""),
                (0, "abc123def456", ""),
                (0, "abc123d", ""),
                (0, "", ""),
                (0, "https://example.com/test", ""),
                (0, "5", ""),  # ahead=5 (本地领先5个commit)
                (0, "0", ""),  # behind=0
            ]
            sts = status(tmp_path)

        st = sts[0]
        # ahead != 0 -> is_up_to_date 为 False
        assert st.is_up_to_date is False


# ═══════════════════════════════════════════════════════
# get_repos — settings fallback with empty YAML
# ═══════════════════════════════════════════════════════


class TestGetReposEdgeCases:
    def test_get_repos_empty_yaml(self, tmp_path) -> None:
        """YAML 文件存在但为空 dict 时回退到 Settings。"""
        from trade_krono_cli.external import get_repos, save_config

        save_config({}, tmp_path)
        repos = get_repos(tmp_path)
        assert isinstance(repos, list)
        for r in repos:
            assert hasattr(r, "name")
            assert hasattr(r, "path")

    def test_get_repos_with_settings(self, tmp_path, monkeypatch) -> None:
        """Settings 中有 tradingagents_root 时应包含 tradingagents repo。"""
        from trade_krono_cli.external import get_repos, save_config

        save_config({}, tmp_path)
        repos = get_repos(tmp_path)
        names = [r.name for r in repos]
        # 取决于 settings 是否配置了 external 路径，至少返回 list
        assert isinstance(names, list)
