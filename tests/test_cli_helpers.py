"""测试 CLI 核心辅助函数。"""

from __future__ import annotations

from pathlib import Path

import pytest
import typer

from trade_krono_cli.cli_commands._core_helpers import (
    _build_degrade_overrides,
    _build_retry_overrides,
    _sanitize_path,
)


class TestBuildDegradeOverrides:
    """测试降级策略辅助函数。"""

    def test_strict_mode_no_overrides(self) -> None:
        """strict 模式不应产生覆盖。"""
        result = _build_degrade_overrides(degrade_mode="strict")
        assert result == {}

    def test_custom_degrade_mode(self) -> None:
        """自定义降级模式应产生覆盖。"""
        result = _build_degrade_overrides(degrade_mode="ta_only_on_kronos_fail")
        assert result == {"degrade_mode": "ta_only_on_kronos_fail"}

    def test_ta_cache_fallback(self) -> None:
        """启用 TA 缓存回退应产生覆盖。"""
        result = _build_degrade_overrides(ta_cache_fallback=True)
        assert result == {"ta_cache_fallback_enabled": True}

    def test_both_overrides(self) -> None:
        """同时启用多种覆盖。"""
        result = _build_degrade_overrides(
            degrade_mode="ta_only_on_kronos_fail",
            ta_cache_fallback=True,
        )
        assert result == {
            "degrade_mode": "ta_only_on_kronos_fail",
            "ta_cache_fallback_enabled": True,
        }


class TestBuildRetryOverrides:
    """测试重试策略辅助函数。"""

    def test_default_no_overrides(self) -> None:
        """默认参数不应产生覆盖。"""
        result = _build_retry_overrides()
        assert result == {}

    def test_custom_max_retries(self) -> None:
        """自定义最大重试次数。"""
        result = _build_retry_overrides(max_retries=5)
        assert result == {"retry_max_attempts": 5}

    def test_custom_base_delay(self) -> None:
        """自定义基础延迟。"""
        result = _build_retry_overrides(base_delay=3.0)
        assert result == {"retry_base_delay": 3.0}

    def test_disable_jitter(self) -> None:
        """禁用抖动。"""
        result = _build_retry_overrides(no_jitter=True)
        assert result == {"retry_jitter": False}

    def test_disable_rate_limit_backoff(self) -> None:
        """禁用以限流退避。"""
        result = _build_retry_overrides(no_rate_limit_backoff=True)
        assert result == {"retry_rate_limit_backoff": False}

    def test_all_overrides(self) -> None:
        """所有选项同时启用。"""
        result = _build_retry_overrides(
            max_retries=5,
            base_delay=3.0,
            no_jitter=True,
            no_rate_limit_backoff=True,
        )
        assert result == {
            "retry_max_attempts": 5,
            "retry_base_delay": 3.0,
            "retry_jitter": False,
            "retry_rate_limit_backoff": False,
        }


class TestSanitizePath:
    """测试路径安全校验。"""

    def test_valid_relative_path(self, tmp_path: Path) -> None:
        """相对路径在项目内应通过。"""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        output_dir = project_dir / "outputs" / "results"
        output_dir.mkdir(parents=True)
        result = _sanitize_path(str(output_dir), "test", project_dir)
        assert result == output_dir

    def test_valid_absolute_path(self, tmp_path: Path) -> None:
        """绝对路径在项目内应通过。"""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        output_dir = project_dir / "outputs"
        output_dir.mkdir()
        result = _sanitize_path(str(output_dir), "test", project_dir)
        assert result == output_dir

    def test_path_traversal_blocked(self, tmp_path: Path) -> None:
        """路径遍历攻击应被阻止。"""
        with pytest.raises(typer.Exit):
            _sanitize_path(str(tmp_path.parent / "etc"), "test", tmp_path)
