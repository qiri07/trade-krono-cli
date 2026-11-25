"""CLI 命令 a_stock_screen 单元测试。

mock 掉 `scripts.a_stock_screen.screen_a_stock_candidates` 及元数据/日期工具，
仅验证 CLI 参数分支（缺参退出 / 正常筛选流程），不触及真实数据源。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from trade_krono_cli.cli import app


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


_MOD = "trade_krono_cli.cli_commands.a_stock_screen"


def _make_result(code: str = "600519", passed: bool = True) -> MagicMock:
    r = MagicMock()
    r.code = code
    r.name = "贵州茅台"
    r.close = 1500.0
    r.market_cap = 22000000000.0
    r.pe_ttm = 25.0
    r.pb = 8.0
    r.turnover_20d_avg = 500000000.0
    r.turnover_rate_12m_avg = 0.5
    r.listing_days = 3000
    r.suspension_days_30d = 0
    r.is_st = False
    r.is_etf = False
    r.is_bse = False
    r.pass_count = 5
    r.total_rules = 5
    r.fail_reasons = []
    r.passed = passed
    r.to_dict.return_value = {
        "code": code,
        "name": r.name,
        "passed": passed,
    }
    return r


class TestAStockScreen:
    """a-stock-screen 命令参数分支与正常流程。"""

    def test_missing_codes_and_all_exits_1(self, runner: CliRunner) -> None:
        """既不给 --codes 也不给 --all，应提示并退出 1。"""
        with patch(f"{_MOD}.expected_trading_day", return_value="2026-08-11"):
            result = runner.invoke(app, ["a-stock-screen", "--date", "2026-08-11"])
            assert result.exit_code == 1
            assert "必须指定 --codes 或 --all" in result.output

    def test_normal_flow_with_codes(self, runner: CliRunner) -> None:
        """给定 --codes 时应调用筛选并正常退出 0。"""
        detail = {"600519": _make_result("600519", True)}
        with (
            patch(f"{_MOD}.expected_trading_day", return_value="2026-08-11"),
            patch(f"{_MOD}.load_metadata_index", return_value={}),
            patch(
                f"{_MOD}.screen_a_stock_candidates",
                return_value=(["600519"], detail),
            ),
        ):
            result = runner.invoke(
                app,
                ["a-stock-screen", "--codes", "600519", "--date", "2026-08-11"],
            )
            assert result.exit_code == 0
            assert "A股筛选结果摘要" in result.output

    def test_all_stocks_branch(self, runner: CliRunner) -> None:
        """--all 分支应调用 get_all_tickers 再筛选。"""
        detail = {"000858": _make_result("000858", False)}
        with (
            patch(f"{_MOD}.expected_trading_day", return_value="2026-08-11"),
            patch(f"{_MOD}.get_all_tickers", return_value=["000858"]),
            patch(f"{_MOD}.load_metadata_index", return_value={}),
            patch(
                f"{_MOD}.screen_a_stock_candidates",
                return_value=([], detail),
            ),
        ):
            result = runner.invoke(
                app,
                ["a-stock-screen", "--all", "--date", "2026-08-11"],
            )
            assert result.exit_code == 0
            assert "A股筛选结果摘要" in result.output
