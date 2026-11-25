"""CLI 命令 export_daily_pv 单元测试。

mock 掉 `get_cache` 及其 `export_daily_pv`，仅验证 CLI 参数分布
（--no-h5 / --debug-stocks / 默认流程）与正常退出，不触及文件系统。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from trade_krono_cli.cli import app


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


_MOD = "trade_krono_cli.cli_commands.export_daily_pv"
_CACHE_MOD = "trade_krono_cli.cache"


def _make_cache() -> MagicMock:
    cache = MagicMock()
    cache.export_daily_pv.return_value = {
        "stocks": 123,
        "rows": 4567,
        "date_min": "2020-01-02",
        "date_max": "2026-08-11",
        "parquet_path": "/tmp/daily_pv.parquet",
        "h5_path": "/tmp/daily_pv.h5",
        "h5_size_mb": 42,
    }
    return cache


class TestExportDailyPV:
    """export-daily-pv 命令参数分支与正常流程。"""

    def test_default_flow(self, runner: CliRunner) -> None:
        """默认参数应调用 cache.export_daily_pv 并退出 0。"""
        cache = _make_cache()
        with (
            patch(f"{_MOD}._load_env"),
            patch(f"{_CACHE_MOD}.get_cache", return_value=cache),
        ):
            result = runner.invoke(app, ["export-daily-pv"])
            assert result.exit_code == 0
            assert "导出完成" in result.output
            assert "股票数 : 123" in result.output
            cache.export_daily_pv.assert_called_once()
            kwargs = cache.export_daily_pv.call_args.kwargs
            assert "h5_path" in kwargs
            assert kwargs.get("debug_insts") == 100

    def test_no_h5_skips_h5_path(self, runner: CliRunner) -> None:
        """--no-h5 时不应传 h5_path。"""
        cache = _make_cache()
        with (
            patch(f"{_MOD}._load_env"),
            patch(f"{_CACHE_MOD}.get_cache", return_value=cache),
        ):
            result = runner.invoke(app, ["export-daily-pv", "--no-h5"])
            assert result.exit_code == 0
            kwargs = cache.export_daily_pv.call_args.kwargs
            assert "h5_path" not in kwargs
            assert "跳过（--no-h5）" in result.output

    def test_debug_stocks(self, runner: CliRunner) -> None:
        """--debug-stocks 应透传 debug_insts。"""
        cache = _make_cache()
        with (
            patch(f"{_MOD}._load_env"),
            patch(f"{_CACHE_MOD}.get_cache", return_value=cache),
        ):
            result = runner.invoke(app, ["export-daily-pv", "--debug-stocks", "50"])
            assert result.exit_code == 0
            kwargs = cache.export_daily_pv.call_args.kwargs
            assert kwargs.get("debug_insts") == 50
