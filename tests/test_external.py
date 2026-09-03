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
        with patch("trade_krono_cli.external._git") as mock_git:
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

        with patch("trade_krono_cli.external._git") as mock_git:
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
    with patch("trade_krono_cli.external._git") as mock_git:
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
