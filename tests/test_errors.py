"""测试错误隔离模块（Phase 3）。"""
import pytest
from trade_krono_cli.errors import (
    ModuleError,
    ModuleResult,
    safe_run,
)


class TestModuleError:
    def test_basic(self):
        err = ModuleError(module="kronos", message="timeout")
        assert err.module == "kronos"
        assert err.message == "timeout"
        assert "kronos" in str(err)

    def test_with_original_exception(self):
        orig = ValueError("bad value")
        err = ModuleError(
            module="ta",
            message="parse failed",
            original_exception=orig,
        )
        assert err.original_exception is orig
        d = err.to_dict()
        assert d["module"] == "ta"
        assert "ValueError" in d["original"]

    def test_with_context(self):
        err = ModuleError(
            module="kronos",
            message="OOM",
            context={"memory_mb": 16000, "batch_size": 64},
        )
        d = err.to_dict()
        assert d["context"]["memory_mb"] == 16000


class TestModuleResult:
    def test_success(self):
        r = ModuleResult(success=True, data=[1, 2, 3])
        assert r.is_ok()
        assert r.data == [1, 2, 3]
        assert r.error is None

    def test_failure(self):
        err = ModuleError(module="ta", message="network error")
        r = ModuleResult(success=False, error=err)
        assert not r.is_ok()
        assert r.error is not None

    def test_to_dict_success(self):
        r = ModuleResult(success=True, data=["a", "b", "c"], elapsed_sec=1.5)
        d = r.to_dict()
        assert d["success"] is True
        assert d["elapsed_sec"] == 1.5
        assert "data_summary" in d

    def test_to_dict_failure(self):
        err = ModuleError(module="kronos", message="bad")
        r = ModuleResult(success=False, error=err, elapsed_sec=0.3)
        d = r.to_dict()
        assert d["success"] is False
        assert "error" in d


class TestSafeRun:
    def test_success_case(self):
        def add(a, b):
            return a + b

        result = safe_run(add, 3, 4, module="test")
        assert result.is_ok()
        assert result.data == 7

    def test_exception_caught(self):
        def fail():
            raise RuntimeError("intentional failure")

        result = safe_run(fail, module="failing")
        assert not result.is_ok()
        assert result.error is not None
        assert result.error.module == "failing"
        assert "intentional failure" in result.error.message

    def test_kwargs_passed(self):
        def greet(name, greeting="hello"):
            return f"{greeting}, {name}"

        result = safe_run(greet, "world", greeting="hi", module="greet")
        assert result.is_ok()
        assert result.data == "hi, world"

    def test_elapsed_time_tracked(self):
        import time

        def slow():
            time.sleep(0.05)
            return "done"

        result = safe_run(slow, module="timing")
        assert result.elapsed_sec > 0.04
