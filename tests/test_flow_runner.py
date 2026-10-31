"""tests for scripts.flow_runner — step 注册表与执行器。"""

from __future__ import annotations

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

    def test_dry_run_returns_zero_without_executing(self, monkeypatch) -> None:
        executed = {"count": 0}

        def _fake_run(cmd, **kwargs):
            executed["count"] += 1

            class _R:
                returncode = 0

            return _R()

        monkeypatch.setattr(fr.subprocess, "run", _fake_run)
        # 强制 dry_run 路径
        monkeypatch.setattr(fr, "DRY_RUN", False)
        step = fr.get_step("check")
        rc = fr.run_step(step, dry_run=True)
        assert rc == 0
        assert executed["count"] == 0, "dry-run must not invoke subprocess"

    def test_run_executes_subprocess(self, monkeypatch) -> None:
        executed = {"count": 0}

        class _R:
            returncode = 0

        def _fake_run(cmd, **kwargs):
            executed["count"] += 1
            return _R()

        monkeypatch.setattr(fr.subprocess, "run", _fake_run)
        monkeypatch.setattr(fr, "DRY_RUN", False)
        step = fr.get_step("check")
        rc = fr.run_step(step, dry_run=False)
        assert rc == 0
        assert executed["count"] == 1

    def test_run_returns_nonzero_on_failure(self, monkeypatch) -> None:
        class _R:
            returncode = 3

        monkeypatch.setattr(fr.subprocess, "run", lambda *a, **k: _R())
        monkeypatch.setattr(fr, "DRY_RUN", False)
        step = fr.get_step("check")
        assert fr.run_step(step, dry_run=False) == 3

    def test_run_step_passes_step_timeout(self, monkeypatch) -> None:
        captured: dict = {}

        def _fake_run(cmd, **kwargs):
            captured.update(kwargs)

            class _R:
                returncode = 0

            return _R()

        monkeypatch.setattr(fr.subprocess, "run", _fake_run)
        monkeypatch.setattr(fr, "DRY_RUN", False)
        step = fr.get_step("buffett")
        assert fr.run_step(step, dry_run=False) == 0
        assert captured.get("timeout") == 5400


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
        rc = fr.cmd_run(step=None, all_steps=True, strict=False)
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
