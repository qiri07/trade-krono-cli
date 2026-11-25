"""CLI repo 子命令单元测试。

覆盖 repo_status / repo_doctor / repo_update / repo_pin，
所有 external 调用通过 mock 隔离，不触及真实 git 或文件系统。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from trade_krono_cli.cli import app


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# ── repo_status ──────────────────────────────────────────────────────────────


class TestRepoStatus:
    """测试 repo status 命令。"""

    def test_no_repos(self, runner: CliRunner) -> None:
        """无外部 repo 时应打印警告并正常退出。"""
        with patch(
            "trade_krono_cli.external.status",
            return_value=[],
        ):
            result = runner.invoke(app, ["repo", "status"])
            assert result.exit_code == 0
            assert "未检测到外部 repo 配置" in result.output

    def test_with_repos(self, runner: CliRunner) -> None:
        """有 repo 时应打印状态表。"""
        mock_entry = MagicMock()
        mock_entry.name = "kronos"
        mock_entry.path_exists = True
        mock_entry.branch = "master"
        mock_entry.commit = "abc1234567890"
        mock_entry.commit_short = "abc12345"
        mock_entry.is_pinned = True
        mock_entry.is_locked = True
        mock_entry.is_dirty = False
        mock_entry.is_git_repo = True
        mock_entry.lock_mismatch = False
        mock_entry.error = None
        mock_entry.is_up_to_date = True

        with patch(
            "trade_krono_cli.external.status",
            return_value=[mock_entry],
        ):
            result = runner.invoke(app, ["repo", "status"])
            assert result.exit_code == 0
            assert "kronos" in result.output
            assert "abc12345" in result.output


# ── repo_doctor ──────────────────────────────────────────────────────────────


class TestRepoDoctor:
    """测试 repo doctor 命令。"""

    def test_all_healthy(self, runner: CliRunner) -> None:
        """无问题时应打印正常提示。"""
        mock_entry = MagicMock()
        mock_entry.name = "kronos"
        mock_entry.is_pinned = True
        mock_entry.commit = "abc1234567890"
        mock_entry.is_locked = False
        mock_entry.lock_commit = None
        mock_entry.branch = "master"
        mock_entries = [mock_entry]
        with (
            patch(
                "trade_krono_cli.external.doctor",
                return_value=[],
            ),
            patch(
                "trade_krono_cli.external.status",
                return_value=mock_entries,
            ),
            patch(
                "trade_krono_cli.external.load_lock",
                return_value={"generated_at": "2026-01-01"},
            ),
        ):
            result = runner.invoke(app, ["repo", "doctor"])
            assert result.exit_code == 0
            assert "状态正常" in result.output

    def test_with_issues(self, runner: CliRunner) -> None:
        """有问题时应列出并退出码为 1。"""
        mock_entry = MagicMock()
        mock_entry.name = "kronos"
        mock_entry.branch = "master"
        with (
            patch(
                "trade_krono_cli.external.doctor",
                return_value=["[kronos] ❌ 路径不存在"],
            ),
            patch(
                "trade_krono_cli.external.status",
                return_value=[mock_entry],
            ),
            patch(
                "trade_krono_cli.external.load_lock",
                return_value={},
            ),
        ):
            result = runner.invoke(app, ["repo", "doctor"])
            assert result.exit_code == 1
            assert "检测到以下问题" in result.output
            assert "❌ 路径不存在" in result.output


# ── repo_update ──────────────────────────────────────────────────────────────


class TestRepoUpdate:
    """测试 repo update 命令。"""

    def test_all_pinned(self, runner: CliRunner) -> None:
        """全部 pinned 时应跳过 update。"""
        mock_repo = MagicMock()
        mock_repo.name = "kronos"
        mock_repo.commit = "abc123"

        with (
            patch(
                "trade_krono_cli.external.get_repos",
                return_value=[mock_repo],
            ),
            patch(
                "trade_krono_cli.external.update",
                return_value={},
            ),
        ):
            result = runner.invoke(app, ["repo", "update"])
            assert result.exit_code == 0
            assert "已 pinned" in result.output

    def test_update_results(self, runner: CliRunner) -> None:
        """有 unpinned repo 时应显示 update 结果。"""
        mock_repo = MagicMock()
        mock_repo.name = "kronos"
        mock_repo.commit = None

        with (
            patch(
                "trade_krono_cli.external.get_repos",
                return_value=[mock_repo],
            ),
            patch(
                "trade_krono_cli.external.update",
                return_value={"kronos": "✅ 已是最新（lock 已刷新）"},
            ),
        ):
            result = runner.invoke(app, ["repo", "update"])
            assert result.exit_code == 0
            assert "已是最新" in result.output


# ── repo_pin ─────────────────────────────────────────────────────────────────


class TestRepoPin:
    """测试 repo pin 命令。"""

    def test_pin_success(self, runner: CliRunner) -> None:
        """pin 成功时应打印确认。"""
        with patch(
            "trade_krono_cli.external.pin",
            return_value=True,
        ):
            result = runner.invoke(
                app,
                ["repo", "pin", "--name", "kronos", "--commit", "abc1234567890"],
            )
            assert result.exit_code == 0
            assert "已 pin 到" in result.output

    def test_pin_unknown_repo(self, runner: CliRunner) -> None:
        """未知 repo 应报错退出。"""
        with patch(
            "trade_krono_cli.external.pin",
            side_effect=ValueError("未知 repo: foo"),
        ):
            result = runner.invoke(
                app,
                ["repo", "pin", "--name", "foo", "--commit", "abc123"],
            )
            assert result.exit_code == 1
            assert "未知 repo: foo" in result.output


# ── repo 子命令注册 ──────────────────────────────────────────────────────────


class TestRepoCommandRegistration:
    """测试 repo 子命令已注册到 CLI。"""

    def test_repo_subcommands(self, runner: CliRunner) -> None:
        """repo status/doctor/update/pin 均可调用 --help。"""
        for subcmd in ["status", "doctor", "update", "pin"]:
            result = runner.invoke(app, ["repo", subcmd, "--help"])
            assert result.exit_code == 0

    def test_repo_no_subcommand(self, runner: CliRunner) -> None:
        """repo 不带子命令时 typer 退出码为 2（缺少必选子命令）。"""
        result = runner.invoke(app, ["repo"])
        assert result.exit_code == 2
