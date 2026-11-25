"""CLI 命令 run 单元测试。

通过 mock 重型依赖（QuantPipeline / PipelineConfig / _load_tickers 等），
仅验证 CLI 参数校验、股票列表来源分支及正常流程分发，不触及真实
数据源、模型或网络。
"""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from trade_krono_cli.cli import app


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# 源模块 patch 路径（run() 内部 lazy import 自这些模块）
_CORE = "trade_krono_cli.cli_commands._core_helpers"
_PIPELINE = "trade_krono_cli.pipeline"
_PIPELINE_CONFIG = "trade_krono_cli.pipeline_config"
_REPORTER = "trade_krono_cli.pipeline.reporter"
_CONFIG_LOADER = "trade_krono_cli.pipeline.config_loader"
_UNIVERSE = "trade_krono_cli.universe.engine"
_FILTERS = "trade_krono_cli.configs.filters"


def _make_config_mock() -> MagicMock:
    mock_config = MagicMock()
    mock_config.default.return_value = mock_config
    mock_config.load.return_value = mock_config
    mock_config.override.return_value = mock_config
    return mock_config


def _patch_run_common() -> ExitStack:
    """给 run() 命令打通全部外部依赖。

    返回一个需由调用方用 ``with`` 管理的 ExitStack，确保所有补丁在
    测试结束后被可靠清理，不会污染后续测试。
    """
    stack = ExitStack()
    stack.enter_context(patch(f"{_CORE}._load_env"))
    stack.enter_context(patch(f"{_CORE}._load_tickers", return_value=["600519"]))
    stack.enter_context(
        patch(f"{_CORE}._sanitize_path", side_effect=lambda p, kind, root: p)
    )
    stack.enter_context(patch(f"{_CORE}._build_retry_overrides", return_value={}))
    stack.enter_context(patch(f"{_CORE}._build_degrade_overrides", return_value={}))
    stack.enter_context(
        patch(f"{_PIPELINE_CONFIG}.PipelineConfig", _make_config_mock())
    )
    mock_pipeline = MagicMock()
    mock_pipeline.run_parallel.return_value = []
    stack.enter_context(
        patch(f"{_PIPELINE}.QuantPipeline", return_value=mock_pipeline)
    )
    stack.enter_context(patch(f"{_REPORTER}.print_table"))
    stack.enter_context(patch(f"{_REPORTER}.print_summary"))
    return stack


class TestRunParamValidation:
    """run 命令运行时参数校验。"""

    def test_min_confidence_out_of_range(self, runner: CliRunner) -> None:
        """--min-confidence 超出 [0, 100] 应退出 1。"""
        with _patch_run_common():
            result = runner.invoke(
                app,
                ["run", "--tickers", "600519", "--date", "2026-08-11", "--min-confidence", "150"],
            )
            assert result.exit_code == 1
            assert "参数校验失败" in result.output

    def test_pred_len_out_of_range(self, runner: CliRunner) -> None:
        """--pred-len 超出 [1, 365] 应退出 1。"""
        with _patch_run_common():
            result = runner.invoke(
                app,
                ["run", "--tickers", "600519", "--date", "2026-08-11", "--pred-len", "400"],
            )
            assert result.exit_code == 1

    def test_lookback_out_of_range(self, runner: CliRunner) -> None:
        """--lookback 超出 [10, 2000] 应退出 1。"""
        with _patch_run_common():
            result = runner.invoke(
                app,
                ["run", "--tickers", "600519", "--date", "2026-08-11", "--lookback", "5"],
            )
            assert result.exit_code == 1

    def test_sample_count_out_of_range(self, runner: CliRunner) -> None:
        """--sample-count 超出 [1, 50] 应退出 1。"""
        with _patch_run_common():
            result = runner.invoke(
                app,
                ["run", "--tickers", "600519", "--date", "2026-08-11", "--sample-count", "100"],
            )
            assert result.exit_code == 1

    def test_invalid_scoring_strategy(self, runner: CliRunner) -> None:
        """非法 scoring-strategy 应退出 1。"""
        with _patch_run_common():
            result = runner.invoke(
                app,
                ["run", "--tickers", "600519", "--date", "2026-08-11", "--scoring-strategy", "nope"],
            )
            assert result.exit_code == 1

    def test_invalid_risk_boost_multiplier(self, runner: CliRunner) -> None:
        """risk-boost-multiplier 超出 (0, 5.0] 应退出 1。"""
        with _patch_run_common():
            result = runner.invoke(
                app,
                [
                    "run",
                    "--tickers",
                    "600519",
                    "--date",
                    "2026-08-11",
                    "--risk-boost-multiplier",
                    "9",
                ],
            )
            assert result.exit_code == 1

    def test_invalid_degrade_mode(self, runner: CliRunner) -> None:
        """非法 degrade-mode 应退出 1。"""
        with _patch_run_common():
            result = runner.invoke(
                app,
                [
                    "run",
                    "--tickers",
                    "600519",
                    "--date",
                    "2026-08-11",
                    "--degrade-mode",
                    "weird",
                ],
            )
            assert result.exit_code == 1


class TestRunTickerSource:
    """run 命令股票列表来源分支。"""

    def test_no_tickers_and_no_auto_universe_exits_1(self, runner: CliRunner) -> None:
        """既不给 --tickers 也不开 --auto-universe，_load_tickers 返回空 → 退出 1。"""
        with (
            patch(f"{_CORE}._load_env"),
            patch(f"{_CORE}._load_tickers", return_value=[]),
            patch(f"{_CORE}._sanitize_path", side_effect=lambda p, kind, root: p),
            patch(f"{_CORE}._build_retry_overrides", return_value={}),
            patch(f"{_CORE}._build_degrade_overrides", return_value={}),
            patch(f"{_PIPELINE_CONFIG}.PipelineConfig", _make_config_mock()),
            patch(f"{_PIPELINE}.QuantPipeline"),
        ):
            result = runner.invoke(app, ["run", "--date", "2026-08-11"])
            assert result.exit_code == 1
            assert "股票列表为空" in result.output

    def test_normal_flow_invokes_pipeline(self, runner: CliRunner) -> None:
        """正常参数下应构造 QuantPipeline 并调用 run_parallel，退出 0。"""
        mock_pipeline = MagicMock()
        mock_pipeline.run_parallel.return_value = []
        with _patch_run_common(), patch(
            f"{_PIPELINE}.QuantPipeline", return_value=mock_pipeline
        ):
            result = runner.invoke(
                app,
                ["run", "--tickers", "600519,000858", "--date", "2026-08-11"],
            )
            assert result.exit_code == 0
            mock_pipeline.run_parallel.assert_called_once()
            assert "完成" in result.output

    def test_auto_universe_branch(self, runner: CliRunner) -> None:
        """--auto-universe 走 UniverseEngine 分支（mock 掉 engine.run）。"""
        mock_pipeline = MagicMock()
        mock_pipeline.run_parallel.return_value = []
        mock_engine = MagicMock()
        mock_engine.run.return_value = ["600519"]
        with (
            _patch_run_common(),
            patch(f"{_PIPELINE}.QuantPipeline", return_value=mock_pipeline),
            patch(f"{_UNIVERSE}.UniverseEngine") as mock_engine_cls,
        ):
            mock_engine_cls.from_config.return_value = mock_engine
            with patch(f"{_FILTERS}.FilterConfig"):
                        result = runner.invoke(
                            app,
                            ["run", "--auto-universe", "--date", "2026-08-11"],
                        )
                        assert result.exit_code == 0
                        mock_engine_cls.from_config.assert_called_once()
                        mock_engine.run.assert_called_once()
                        mock_pipeline.run_parallel.assert_called_once()
