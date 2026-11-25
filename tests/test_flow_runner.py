"""tests for scripts.flow_runner — step 注册表与执行器。"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import scripts.flow_runner as fr

if TYPE_CHECKING:
    from pathlib import Path


class TestRegistry:
    """STEPS 注册表结构。"""

    def test_all_steps_have_unique_names(self) -> None:
        names = fr.all_step_names()
        assert len(names) == len(set(names))

    def test_expected_steps_present(self) -> None:
        names = set(fr.all_step_names())
        for expected in [
            "check",
            "sync",
            "sync_etf",
            "metadata",
            "buffett",
            "resonance",
            "multi_head",
            "whitelist",
            "double_ma",
            "macd_ma",
            "combined",
            "intraday",
            "export",
        ]:
            assert expected in names, f"missing step {expected}"

    def test_export_disabled_by_default(self) -> None:
        assert fr.get_step("export").enabled_by_default is False

    def test_get_step_unknown_returns_none(self) -> None:
        assert fr.get_step("nonexistent") is None

    def test_step_command_is_list(self) -> None:
        s = fr.get_step("sync")
        assert isinstance(s.command, list)
        assert all(isinstance(x, str) for x in s.command)

    def test_long_running_steps_have_explicit_timeout(self) -> None:
        for name, expected in [("buffett", 5400), ("sync", 7200), ("sync_etf", 7200)]:
            step = fr.get_step(name)
            assert step is not None
            assert step.timeout == expected

    def test_fivemust_command_never_passes_push_flag(self) -> None:
        """a_stock_screen/cli.py 只认 --no-push，误传 --push N 会 argparse exit 2。

        该失败曾导致 combined 因前置不可用被跳过，此处加回归护栏。
        """
        step = fr.get_step("fivemust")
        assert step is not None
        assert "--fivemust" in step.command
        # --no-push 是合法的；--push（裸）不是，且必须永远不出现
        assert "--push" not in step.command
        # NO_PUSH=1（PUSH_FLAG=0）时以 --no-push 结尾；否则不含任何 push 参数
        if "--no-push" in step.command:
            assert step.command[-1] == "--no-push"


class TestResolveEnabledSteps:
    """resolve_enabled_steps 的 enable/disable 语义。"""

    def test_default_excludes_export(self) -> None:
        selected = fr.resolve_enabled_steps()
        names = [s.name for s in selected]
        assert "export" not in names

    def test_enable_export_includes_it(self) -> None:
        selected = fr.resolve_enabled_steps(enable=["export"])
        names = [s.name for s in selected]
        assert "export" in names

    def test_disable_removes_step(self) -> None:
        selected = fr.resolve_enabled_steps(disable=["intraday"])
        names = [s.name for s in selected]
        assert "intraday" not in names

    def test_enable_overrides_disable(self) -> None:
        # disable 优先于 enable（显式排除更强）
        selected = fr.resolve_enabled_steps(enable=["export"], disable=["export"])
        names = [s.name for s in selected]
        assert "export" not in names

    def test_dependencies_respected_in_order(self) -> None:
        selected = fr.resolve_enabled_steps()
        names = [s.name for s in selected]
        # check 在 sync 之前，sync 在 buffett 之前
        assert names.index("check") < names.index("sync")
        assert names.index("sync") < names.index("buffett")

    def test_empty_when_all_disabled(self) -> None:
        disable_all = fr.all_step_names()
        selected = fr.resolve_enabled_steps(disable=disable_all)
        assert selected == []


class TestRunStep:
    """run_step 执行与 dry-run 行为。"""

    class _FakeProc:
        """模拟 Popen 进程：记录 wait 的超时参数并返回预设 exit code。"""

        def __init__(self, rc: int = 0, recorder: dict | None = None) -> None:
            self.pid = 4242
            self._rc = rc
            self._recorder = recorder if recorder is not None else {}

        def wait(self, timeout=None):  # noqa: ANN001 - 模拟 Popen.wait 签名
            self._recorder["timeout"] = timeout
            return self._rc

    def test_dry_run_returns_zero_without_executing(self, monkeypatch) -> None:
        executed = {"count": 0}

        def _fake_popen(*_a, **_k):
            executed["count"] += 1
            raise AssertionError("dry-run 不应启动子进程")

        monkeypatch.setattr(fr.subprocess, "Popen", _fake_popen)
        # 强制 dry_run 路径
        monkeypatch.setattr(fr, "DRY_RUN", False)
        step = fr.get_step("check")
        rc = fr.run_step(step, dry_run=True)
        assert rc == 0
        assert executed["count"] == 0, "dry-run must not invoke subprocess"

    def test_run_executes_subprocess(self, monkeypatch) -> None:
        executed = {"count": 0}

        def _fake_popen(*_a, **_k):
            executed["count"] += 1
            return self._FakeProc(rc=0)

        monkeypatch.setattr(fr.subprocess, "Popen", _fake_popen)
        monkeypatch.setattr(fr, "DRY_RUN", False)
        step = fr.get_step("check")
        rc = fr.run_step(step, dry_run=False)
        assert rc == 0
        assert executed["count"] == 1

    def test_run_returns_nonzero_on_failure(self, monkeypatch) -> None:
        monkeypatch.setattr(fr.subprocess, "Popen", lambda *a, **k: self._FakeProc(rc=3))
        monkeypatch.setattr(fr, "DRY_RUN", False)
        step = fr.get_step("check")
        assert fr.run_step(step, dry_run=False) == 3

    def test_run_step_passes_step_timeout(self, monkeypatch) -> None:
        recorder: dict = {}
        monkeypatch.setattr(
            fr.subprocess, "Popen", lambda *a, **k: self._FakeProc(rc=0, recorder=recorder)
        )
        monkeypatch.setattr(fr, "DRY_RUN", False)
        step = fr.get_step("buffett")
        assert fr.run_step(step, dry_run=False) == 0
        assert recorder.get("timeout") == 5400

    def test_run_step_timeout_kills_process_group(self, monkeypatch) -> None:
        """超时后必须整组 kill 进程（start_new_session 的进程组），避免孤儿进程。"""
        killed: dict = {}

        class _TimeoutProc(self._FakeProc):
            def __init__(self) -> None:
                super().__init__()
                self._waited = False

            def wait(self, timeout=None):  # noqa: ANN001 - 模拟 Popen.wait 签名
                if not self._waited:
                    self._waited = True
                    self._recorder["timeout"] = timeout
                    raise subprocess.TimeoutExpired(cmd="x", timeout=timeout or 1)
                return -9

        monkeypatch.setattr(fr.subprocess, "Popen", lambda *a, **k: _TimeoutProc())
        monkeypatch.setattr(fr.os, "killpg", lambda pg, sig: killed.update(pg=pg, sig=sig))
        monkeypatch.setattr(fr, "DRY_RUN", False)
        step = fr.get_step("check")
        assert fr.run_step(step, dry_run=False) == 124
        assert killed["pg"] == 4242
        assert killed["sig"] == fr.signal.SIGKILL


class TestCmdRun:
    """cmd_run 单步 / 全量 / 未知 step。"""

    def test_unknown_step_returns_2(self) -> None:
        assert fr.cmd_run(step="bogus") == 2

    def test_no_args_returns_2(self) -> None:
        assert fr.cmd_run(step=None, all_steps=False) == 2

    def test_single_step_via_name(self, monkeypatch) -> None:
        called = {}

        def _fake_run_step(step, dry_run=False):
            called["name"] = step.name
            return 0

        monkeypatch.setattr(fr, "run_step", _fake_run_step)
        rc = fr.cmd_run(step="sync")
        assert rc == 0
        assert called["name"] == "sync"

    def test_single_step_records_checkpoint_status(self, monkeypatch) -> None:
        saved: dict = {}

        def _fake_run_step(step, dry_run=False):
            return 0

        monkeypatch.setattr(fr, "run_step", _fake_run_step)
        monkeypatch.setattr(fr, "save_checkpoint", lambda s: saved.update(s))
        rc = fr.cmd_run(step="sync")
        assert rc == 0
        assert saved.get("sync") == "success"

    def test_all_with_strict_stops_on_first_error(self, monkeypatch) -> None:
        seq = iter([0, 5, 0])

        def _fake_run_step(step, dry_run=False):
            return next(seq)

        monkeypatch.setattr(fr, "run_step", _fake_run_step)
        # 让全部 step 都启用，触发 --strict 首错即停
        monkeypatch.setattr(fr, "resolve_enabled_steps", lambda **k: fr.STEPS)
        rc = fr.cmd_run(step=None, all_steps=True, strict=True)
        assert rc == 5

    def test_all_non_strict_continues_and_reports_failures(self, monkeypatch) -> None:
        calls = []

        def _fake_run_step(step, dry_run=False):
            calls.append(step.name)
            return 0 if step.name != "buffett" else 1

        monkeypatch.setattr(fr, "run_step", _fake_run_step)
        monkeypatch.setattr(
            fr,
            "resolve_enabled_steps",
            lambda **k: [s for s in fr.STEPS if s.enabled_by_default],
        )
        rc = fr.cmd_run(step=None, all_steps=True, strict=False, force=True)
        # 非 strict：全部 step 都被调用，整体返回 1（有失败）
        assert "buffett" in calls
        assert rc == 1

    def test_all_skips_dependent_steps_when_dependency_failed(self, monkeypatch) -> None:
        monkeypatch.setattr(fr, "load_checkpoint", lambda: {})
        monkeypatch.setattr(fr, "save_checkpoint", lambda s: None)
        executed: list[str] = []

        def _fake_run_step(step, dry_run: bool = False) -> int:
            executed.append(step.name)
            return 1 if step.name == "sync" else 0

        monkeypatch.setattr(fr, "run_step", _fake_run_step)
        monkeypatch.setattr(
            fr,
            "resolve_enabled_steps",
            lambda **k: [
                s
                for s in fr.STEPS
                if s.enabled_by_default and s.name in ("check", "sync", "buffett")
            ],
        )
        rc = fr.cmd_run(step=None, all_steps=True, strict=False)
        # sync 失败 → buffett 等依赖其失败的 step 被跳过，不执行
        assert rc == 1
        assert executed == ["check", "sync"]
        assert "buffett" not in executed


class TestResume:
    """断点续传（resume）语义。"""

    def test_resume_skips_successful_and_starts_from_failed(self, monkeypatch) -> None:
        statuses = {s.name: "success" for s in fr.STEPS if s.enabled_by_default}
        statuses["check"] = "success"
        statuses["sync"] = "failed"
        monkeypatch.setattr(fr, "load_checkpoint", lambda: statuses)
        monkeypatch.setattr(fr, "save_checkpoint", lambda s: None)
        executed: list[str] = []

        def _fake_run_step(step, dry_run: bool = False) -> int:
            executed.append(step.name)
            return 0

        monkeypatch.setattr(fr, "run_step", _fake_run_step)
        monkeypatch.setattr(
            fr,
            "resolve_enabled_steps",
            lambda **k: [s for s in fr.STEPS if s.enabled_by_default],
        )
        rc = fr.cmd_run(step=None, all_steps=True, resume=True)
        assert rc == 0
        # check 已成功、其余 step 已成功 → 仅 sync 失败，从 sync 处断点后重跑
        assert executed == ["sync"]

    def test_resume_stops_at_first_failure_and_returns_nonzero(self, monkeypatch) -> None:
        monkeypatch.setattr(fr, "load_checkpoint", lambda: {})
        monkeypatch.setattr(fr, "save_checkpoint", lambda s: None)
        executed: list[str] = []

        def _fake_run_step(step, dry_run: bool = False) -> int:
            executed.append(step.name)
            return 1 if step.name == "sync" else 0

        monkeypatch.setattr(fr, "run_step", _fake_run_step)
        monkeypatch.setattr(
            fr,
            "resolve_enabled_steps",
            lambda **k: [
                s
                for s in fr.STEPS
                if s.enabled_by_default and s.name in ("check", "sync", "buffett")
            ],
        )
        rc = fr.cmd_run(step=None, all_steps=True, resume=True)
        assert rc == 1
        # resume 模式下失败即停，不继续执行断点之后的 step
        assert executed == ["check", "sync"]
        assert "buffett" not in executed

    def test_resume_nothing_to_do_when_all_success(self, monkeypatch) -> None:
        monkeypatch.setattr(
            fr,
            "load_checkpoint",
            lambda: {s.name: "success" for s in fr.STEPS if s.enabled_by_default},
        )
        monkeypatch.setattr(fr, "save_checkpoint", lambda s: None)
        called = {"count": 0}

        def _fake_run_step(step, dry_run: bool = False) -> int:
            called["count"] += 1
            return 0

        monkeypatch.setattr(fr, "run_step", _fake_run_step)
        rc = fr.cmd_run(step=None, all_steps=True, resume=True)
        assert rc == 0
        assert called["count"] == 0, "resume 且全部成功时不应执行任何 step"


class TestCheckpoint:
    """checkpoint 加载/保存（断点续传状态文件）."""

    def test_load_checkpoint_missing_file_returns_empty(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("scripts.flow_runner.checkpoint_path", lambda: tmp_path / "nope.json")
        assert fr.load_checkpoint() == {}

    def test_save_and_load_checkpoint_roundtrip(self, tmp_path: Path, monkeypatch) -> None:
        path = tmp_path / "flow_checkpoint_2026-10-31.json"
        monkeypatch.setattr("scripts.flow_runner.checkpoint_path", lambda: path)
        fr.save_checkpoint({"sync": "failed", "buffett": "success"})
        assert fr.load_checkpoint() == {"sync": "failed", "buffett": "success"}

    def test_load_checkpoint_corrupt_json_returns_empty(self, tmp_path: Path, monkeypatch) -> None:
        path = tmp_path / "flow_checkpoint_2026-10-31.json"
        path.write_text("{not valid json", encoding="utf-8")
        monkeypatch.setattr("scripts.flow_runner.checkpoint_path", lambda: path)
        assert fr.load_checkpoint() == {}


class TestBuildArgParser:
    """argparse 构建与子命令分发。"""

    def test_parser_builds_and_dispatches_list(self, capsys) -> None:
        parser = fr.build_arg_parser()
        args = parser.parse_args(["list"])
        rc = args.func(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "export" in out
        assert "DISABLED" in out

    def test_parser_dispatches_run_all_dry(self, capsys) -> None:
        parser = fr.build_arg_parser()
        args = parser.parse_args(["run", "--all", "--dry-run"])
        # dry-run 下不真正跑子进程，但会打印
        rc = args.func(args)
        assert rc == 0


class TestCheckpointStaleCleanup:
    """非 resume 的 --all 应清掉 checkpoint 里本轮未启用 step 的残留状态。"""

    def test_all_non_resume_drops_disabled_step_residue(
        self, monkeypatch
    ) -> None:
        saved: dict[str, str] = {}

        def _fake_run_step(step, dry_run: bool = False) -> int:
            return 0

        monkeypatch.setattr(fr, "run_step", _fake_run_step)
        # 本轮只启用 sync，但当天 checkpoint 残留了旧轮次的 sync_hk: failed
        monkeypatch.setattr(fr, "load_checkpoint", lambda: {"sync_hk": "failed", "check": "success"})
        monkeypatch.setattr(
            fr, "save_checkpoint", lambda s: saved.update(s)
        )
        monkeypatch.setattr(
            fr,
            "resolve_enabled_steps",
            lambda **k: [s for s in fr.STEPS if s.name in ("sync",)],
        )

        assert fr.cmd_run(step=None, all_steps=True, resume=False) == 0
        # 写回 checkpoint 时，本轮未启用的 sync_hk 不应残留
        assert "sync_hk" not in saved
        assert saved.get("sync") == "success"

    def test_all_non_resume_keeps_enabled_step_success(
        self, monkeypatch
    ) -> None:
        """本轮启用的 step 若 checkpoint 已 success，应沿用（避免重跑）。"""
        saved: dict[str, str] = {}

        def _fake_run_step(step, dry_run: bool = False) -> int:
            return 0

        monkeypatch.setattr(fr, "run_step", _fake_run_step)
        monkeypatch.setattr(fr, "load_checkpoint", lambda: {"check": "success", "sync": "success"})
        monkeypatch.setattr(fr, "save_checkpoint", lambda s: saved.update(s))
        monkeypatch.setattr(
            fr,
            "resolve_enabled_steps",
            lambda **k: [s for s in fr.STEPS if s.name in ("check", "sync")],
        )

        fr.cmd_run(step=None, all_steps=True, resume=False)
        assert saved.get("check") == "success"
        assert saved.get("sync") == "success"


class TestUnifiedBackup:
    """backup step 与下游 `--no-backup` 注入（一轮流程只备一次）。"""

    def test_backup_step_registered_and_enabled(self) -> None:
        step = fr.get_step("backup")
        assert step is not None
        assert step.enabled_by_default is True
        assert step.command[-1] == "scripts/backup_cache.py"

    def test_backup_runs_before_sync(self) -> None:
        names = [s.name for s in fr.resolve_enabled_steps()]
        assert names.index("backup") < names.index("sync")

    def test_sync_depends_on_backup(self) -> None:
        sync = fr.get_step("sync")
        assert sync is not None
        assert "backup" in sync.depends_on

    def test_suppress_appends_no_backup(self) -> None:
        for name in ("sync", "sync_etf"):
            step = fr.get_step(name)
            assert step is not None
            suppressed = fr._suppress_auto_backup(step)
            assert suppressed.command[-1] == "--no-backup"
            # Step 为 frozen dataclass，原定义不被改写
            assert "--no-backup" not in step.command
            # 幂等：重复调用不叠加
            assert fr._suppress_auto_backup(suppressed).command == suppressed.command

    def test_suppress_ignores_other_steps(self) -> None:
        step = fr.get_step("buffett")
        assert step is not None
        assert fr._suppress_auto_backup(step) is step

    def test_run_all_injects_no_backup(self, monkeypatch) -> None:
        seen: dict[str, list[str]] = {}

        def _fake_run_step(step, dry_run: bool = False) -> int:
            seen[step.name] = list(step.command)
            return 0

        monkeypatch.setattr(fr, "run_step", _fake_run_step)
        monkeypatch.setattr(fr, "save_checkpoint", lambda s: None)

        # sync_etf 现在默认禁用，需显式 --enable sync_etf 才会执行
        assert fr.cmd_run(step=None, all_steps=True, enable=["sync_etf"], force=True) == 0
        assert seen["sync"][-1] == "--no-backup"
        assert seen["sync_etf"][-1] == "--no-backup"
        assert "--no-backup" not in seen["buffett"]

    def test_run_all_without_backup_step_keeps_auto_backup(self, monkeypatch) -> None:
        seen: dict[str, list[str]] = {}

        def _fake_run_step(step, dry_run: bool = False) -> int:
            seen[step.name] = list(step.command)
            return 0

        monkeypatch.setattr(fr, "run_step", _fake_run_step)
        monkeypatch.setattr(fr, "save_checkpoint", lambda s: None)
        monkeypatch.setattr(
            fr,
            "resolve_enabled_steps",
            lambda **k: [s for s in fr.STEPS if s.name in ("sync", "sync_etf")],
        )

        assert fr.cmd_run(step=None, all_steps=True, force=True) == 0
        assert "--no-backup" not in seen["sync"]
        assert "--no-backup" not in seen["sync_etf"]

    def test_resume_with_backup_already_succeeded_still_suppresses(self, monkeypatch) -> None:
        seen: dict[str, list[str]] = {}

        def _fake_run_step(step, dry_run: bool = False) -> int:
            seen[step.name] = list(step.command)
            return 0

        monkeypatch.setattr(fr, "run_step", _fake_run_step)
        monkeypatch.setattr(fr, "save_checkpoint", lambda s: None)
        monkeypatch.setattr(fr, "load_checkpoint", lambda: {"backup": "success"})
        monkeypatch.setattr(
            fr,
            "resolve_enabled_steps",
            lambda **k: [s for s in fr.STEPS if s.name in ("sync",)],
        )

        assert fr.cmd_run(step=None, all_steps=True, resume=True) == 0
        assert "backup" not in seen  # 已成功，断点跳过
        assert seen["sync"][-1] == "--no-backup"
