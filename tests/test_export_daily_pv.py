"""测试 export-daily-pv CLI 命令。"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from trade_krono_cli.cli import app


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


class TestExportDailyPv:
    """测试 export-daily-pv 命令。"""

    def test_export_daily_pv_command_exists(self, runner: CliRunner) -> None:
        """验证命令存在且可调用。"""
        result = runner.invoke(app, ["export-daily-pv", "--help"])
        assert result.exit_code == 0
        assert "RD-Agent" in result.output or "daily_pv" in result.output.lower()
