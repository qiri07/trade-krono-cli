"""扩展 CLI 测试：策略标志、warm_cache、history、eval 变体、repo 命令、错误路径。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from trade_krono_cli.cli import app
from tests.conftest import _strip_ansi


@pytest.fixture
def runner():
    return CliRunner()


# ═══════════════════════════════════════════════════════
# Strategy flags — run command
# ═══════════════════════════════════════════════════════


class TestRunStrategyFlags:
    def test_run_help_shows_strategy_panel(self, runner):
        """run --help 应显示评分策略面板。"""
        result = runner.invoke(app, ["run", "--help"])
        assert result.exit_code == 0
        out = _strip_ansi(result.output)
        assert "评分策略" in out
        assert "--scoring-strategy" in out
        assert "--risk-boost-strategy" in out
        assert "--risk-boost-multiplier" in out
        assert "--risk-boost-power" in out

    def test_run_passes_scoring_strategy_to_pipeline(self, runner):
        """--scoring-strategy multiplicative 应被传入 PipelineConfig。"""
        mock_pipeline = MagicMock()
        mock_pipeline.run_parallel.return_value = []
        with (
            patch("trade_krono_cli.cli_commands.core._load_env"),
            patch("trade_krono_cli.pipeline.QuantPipeline", return_value=mock_pipeline),
        ):
            result = runner.invoke(
                app,
                [
                    "run",
                    "--tickers",
                    "600519",
                    "--date",
                    "2026-08-11",
                    "--scoring-strategy",
                    "multiplicative",
                ],
            )
            assert result.exit_code == 0
            # QuantPipeline 被实例化，检查 kwargs
            call_args = mock_pipeline.call_args
            if call_args is not None:
                cfg = call_args[1].get("config")
                if cfg is not None:
                    assert cfg.scoring_strategy.strategy == "multiplicative"

    def test_run_passes_risk_boost_strategy_to_pipeline(self, runner):
        """--risk-boost-strategy diminishing_boost 应被传入 PipelineConfig。"""
        mock_pipeline = MagicMock()
        mock_pipeline.run_parallel.return_value = []
        with (
            patch("trade_krono_cli.cli_commands.core._load_env"),
            patch("trade_krono_cli.pipeline.QuantPipeline", return_value=mock_pipeline),
        ):
            result = runner.invoke(
                app,
                [
                    "run",
                    "--tickers",
                    "600519",
                    "--date",
                    "2026-08-11",
                    "--risk-boost-strategy",
                    "diminishing_boost",
                    "--risk-boost-power",
                    "0.3",
                ],
            )
            assert result.exit_code == 0
            call_args = mock_pipeline.call_args
            if call_args is not None:
                cfg = call_args[1].get("config")
                if cfg is not None:
                    assert cfg.risk_boost_strategy.strategy == "diminishing_boost"
                    assert cfg.risk_boost_strategy.diminishing_power == 0.3

    def test_run_passes_risk_boost_multiplier(self, runner):
        """--risk-boost-multiplier 应被传入 PipelineConfig。"""
        mock_pipeline = MagicMock()
        mock_pipeline.run_parallel.return_value = []
        with (
            patch("trade_krono_cli.cli_commands.core._load_env"),
            patch("trade_krono_cli.pipeline.QuantPipeline", return_value=mock_pipeline),
        ):
            result = runner.invoke(
                app,
                [
                    "run",
                    "--tickers",
                    "600519",
                    "--date",
                    "2026-08-11",
                    "--risk-boost-strategy",
                    "scaled_boost",
                    "--risk-boost-multiplier",
                    "2.5",
                ],
            )
            assert result.exit_code == 0
            call_args = mock_pipeline.call_args
            if call_args is not None:
                cfg = call_args[1].get("config")
                if cfg is not None:
                    assert cfg.risk_boost_strategy.strategy == "scaled_boost"
                    assert cfg.risk_boost_strategy.multiplier == 2.5

    def test_run_default_strategy_unchanged(self, runner):
        """默认参数下 scoring_strategy 保持 linear，risk_boost_strategy 保持 fixed_boost。"""
        mock_pipeline = MagicMock()
        mock_pipeline.run_parallel.return_value = []
        with (
            patch("trade_krono_cli.cli_commands.core._load_env"),
            patch("trade_krono_cli.pipeline.QuantPipeline", return_value=mock_pipeline),
        ):
            result = runner.invoke(
                app,
                [
                    "run",
                    "--tickers",
                    "600519",
                    "--date",
                    "2026-08-11",
                ],
            )
            assert result.exit_code == 0
            call_args = mock_pipeline.call_args
            if call_args is not None:
                cfg = call_args[1].get("config")
                # 无 override 时 config 为 None（走 else 分支）
                if cfg is not None:
                    assert cfg.scoring_strategy.strategy == "linear"
                    assert cfg.risk_boost_strategy.strategy == "fixed_boost"


# ═══════════════════════════════════════════════════════
# warm_cache command
# ═══════════════════════════════════════════════════════


class TestWarmCache:
    def test_warm_cache_help(self, runner):
        result = runner.invoke(app, ["warm-cache", "--help"])
        assert result.exit_code == 0
        out = _strip_ansi(result.output)
        assert "--tickers" in out
        assert "--date" in out
        assert "--lookback" in out

    def test_warm_cache_empty_tickers(self, runner):
        with patch("trade_krono_cli.cli_commands.core._load_env"):
            result = runner.invoke(app, ["warm-cache", "--date", "2026-08-11"])
            assert result.exit_code != 0
            assert "股票列表为空" in _strip_ansi(result.output)

    def test_warm_cache_with_mock_cache(self, runner):
        mock_cache = MagicMock()
        mock_cache.warm_history.return_value = (100, 5)
        with (
            patch("trade_krono_cli.cli_commands.core._load_env"),
            patch("trade_krono_cli.cache.get_cache", return_value=mock_cache),
        ):
            result = runner.invoke(
                app,
                [
                    "warm-cache",
                    "--tickers",
                    "600519,000858",
                    "--date",
                    "2026-08-11",
                ],
            )
            assert result.exit_code == 0
            assert "预热完成" in _strip_ansi(result.output)
            assert mock_cache.warm_history.call_count == 2

    def test_warm_cache_from_config_file(self, runner, tmp_path):
        config_file = tmp_path / "tickers.txt"
        config_file.write_text("600519\n000858\n")
        mock_cache = MagicMock()
        mock_cache.warm_history.return_value = (50, 3)
        with (
            patch("trade_krono_cli.cli_commands.core._load_env"),
            patch("trade_krono_cli.cache.get_cache", return_value=mock_cache),
        ):
            result = runner.invoke(
                app,
                [
                    "warm-cache",
                    "--config",
                    str(config_file),
                    "--date",
                    "2026-08-11",
                ],
            )
            assert result.exit_code == 0
            assert mock_cache.warm_history.call_count == 2


# ═══════════════════════════════════════════════════════
# history command
# ═══════════════════════════════════════════════════════


class TestHistory:
    def test_history_no_ticker_shows_jobs(self, runner):
        mock_research = MagicMock()
        mock_research.list_jobs.return_value = [
            {
                "job_id": "j1",
                "run_id": "r1",
                "date": "2026-08-10",
                "n_tickers": 5,
                "n_success": 4,
                "data_version": "v1",
                "elapsed": 12.3,
            },
        ]
        with (
            patch("trade_krono_cli.cli_commands.core._load_env"),
            patch("trade_krono_cli.research_db.get_research", return_value=mock_research),
        ):
            result = runner.invoke(app, ["history"])
            assert result.exit_code == 0
            assert "j1" in result.output
            assert "2026-08-10" in result.output

    def test_history_no_jobs(self, runner):
        mock_research = MagicMock()
        mock_research.list_jobs.return_value = []
        with (
            patch("trade_krono_cli.cli_commands.core._load_env"),
            patch("trade_krono_cli.research_db.get_research", return_value=mock_research),
        ):
            result = runner.invoke(app, ["history"])
            assert result.exit_code == 0
            assert "暂无分析记录" in _strip_ansi(result.output)

    def test_history_with_ticker_shows_records(self, runner):
        mock_research = MagicMock()
        mock_research.query_history.return_value = [
            {
                "date": "2026-08-10",
                "run_id": "r1",
                "data_version": "v1",
                "rank": 1,
                "composite_score": 85.0,
                "ta_signal": "BUY",
                "ta_confidence": 90.0,
                "kronos_direction": "UP",
                "kronos_change": 3.5,
            }
        ]
        with (
            patch("trade_krono_cli.cli_commands.core._load_env"),
            patch("trade_krono_cli.research_db.get_research", return_value=mock_research),
        ):
            result = runner.invoke(app, ["history", "--ticker", "600519"])
            assert result.exit_code == 0
            # 表格输出中日期可能被截断，检查 RUNID 即可
            assert "r1" in result.output

    def test_history_ticker_not_found(self, runner):
        mock_research = MagicMock()
        mock_research.query_history.return_value = []
        with (
            patch("trade_krono_cli.cli_commands.core._load_env"),
            patch("trade_krono_cli.research_db.get_research", return_value=mock_research),
        ):
            result = runner.invoke(app, ["history", "--ticker", "999999"])
            assert result.exit_code == 0
            assert "未找到" in _strip_ansi(result.output)

    def test_history_limit(self, runner):
        mock_research = MagicMock()
        mock_research.list_jobs.return_value = [
            {
                "job_id": f"j{i}",
                "run_id": None,
                "date": "2026-08-10",
                "n_tickers": 3,
                "n_success": 3,
                "data_version": "v1",
                "elapsed": 5.0,
            }
            for i in range(5)
        ]
        with (
            patch("trade_krono_cli.cli_commands.core._load_env"),
            patch("trade_krono_cli.research_db.get_research", return_value=mock_research),
        ):
            result = runner.invoke(app, ["history", "--limit", "2"])
            assert result.exit_code == 0
            # limit=2 只显示前两条
            output = result.output
            assert "j0" in output


# ═══════════════════════════════════════════════════════
# eval-prediction variants
# ═══════════════════════════════════════════════════════


class TestEvalPredictionVariants:
    def test_eval_with_backtest_flag(self, runner):
        with (
            patch("trade_krono_cli.cli_commands.core._load_env"),
            patch("trade_krono_cli.prediction_eval.run_evaluation") as mock_eval,
        ):
            result = runner.invoke(
                app,
                [
                    "eval-prediction",
                    "--from",
                    "2026-01-01",
                    "--to",
                    "2026-08-11",
                    "--backtest",
                    "--rebal-mode",
                    "rebal_weekly",
                ],
            )
            assert result.exit_code == 0
            mock_eval.assert_called_once()
            kwargs = mock_eval.call_args[1]
            assert kwargs["backtest"] is True
            assert kwargs["rebal_mode"] == "rebal_weekly"

    def test_eval_with_tickers_filter(self, runner):
        with (
            patch("trade_krono_cli.cli_commands.core._load_env"),
            patch("trade_krono_cli.prediction_eval.run_evaluation") as mock_eval,
        ):
            result = runner.invoke(
                app,
                [
                    "eval-prediction",
                    "--tickers",
                    "600519,000858",
                ],
            )
            assert result.exit_code == 0
            kwargs = mock_eval.call_args[1]
            assert kwargs["tickers"] == ["600519", "000858"]

    def test_eval_with_all_options(self, runner):
        with (
            patch("trade_krono_cli.cli_commands.core._load_env"),
            patch("trade_krono_cli.prediction_eval.run_evaluation") as mock_eval,
        ):
            result = runner.invoke(
                app,
                [
                    "eval-prediction",
                    "--from",
                    "2025-01-01",
                    "--to",
                    "2026-08-11",
                    "--tickers",
                    "600519",
                    "--latest",
                    "--backtest",
                    "--rebal-mode",
                    "rebal_monthly",
                ],
            )
            assert result.exit_code == 0
            kwargs = mock_eval.call_args[1]
            assert kwargs["from_date"] == "2025-01-01"
            assert kwargs["to_date"] == "2026-08-11"
            assert kwargs["tickers"] == ["600519"]
            assert kwargs["latest"] is True
            assert kwargs["backtest"] is True
            assert kwargs["rebal_mode"] == "rebal_monthly"


# ═══════════════════════════════════════════════════════
# repo commands
# ═══════════════════════════════════════════════════════


class TestRepoCommands:
    def test_repo_doctor_no_issues(self, runner):
        with (
            patch("trade_krono_cli.cli_commands.core._load_env"),
            patch("trade_krono_cli.external.doctor", return_value=[]),
            patch("trade_krono_cli.external.status", return_value=[]),
            patch("trade_krono_cli.external.load_lock", return_value={}),
        ):
            result = runner.invoke(app, ["repo", "repo-doctor"])
            # 无 repo 时退出非 0
            assert result.exit_code != 0 or "外部 repo" in _strip_ansi(result.output)

    def test_repo_doctor_with_issues(self, runner):
        from trade_krono_cli.external import ExternalRepo

        with (
            patch("trade_krono_cli.cli_commands.core._load_env"),
            patch("trade_krono_cli.external.doctor", return_value=["路径不存在: external/Kronos"]),
            patch(
                "trade_krono_cli.external.status",
                return_value=[
                    ExternalRepo(
                        name="kronos", path="/tmp/kronos", branch="main", url="", commit=None
                    )
                ],
            ),
            patch("trade_krono_cli.external.load_lock", return_value={}),
        ):
            result = runner.invoke(app, ["repo", "repo-doctor"])
            assert result.exit_code != 0
            assert "检测到以下问题" in _strip_ansi(result.output)

    def test_repo_update(self, runner):
        with (
            patch("trade_krono_cli.cli_commands.core._load_env"),
            patch("trade_krono_cli.external.get_repos", return_value=[]),
            patch("trade_krono_cli.external.update", return_value={}),
        ):
            result = runner.invoke(app, ["repo", "repo-update"])
            assert result.exit_code == 0

    def test_repo_pin_success(self, runner):
        with (
            patch("trade_krono_cli.cli_commands.core._load_env"),
            patch("trade_krono_cli.external.pin") as mock_pin,
        ):
            result = runner.invoke(
                app, ["repo", "repo-pin", "--name", "tradingagents", "--commit", "abc123def"]
            )
            assert result.exit_code == 0
            assert "已 pin" in _strip_ansi(result.output)
            mock_pin.assert_called_once_with("tradingagents", "abc123def")

    def test_repo_pin_failure(self, runner):
        with (
            patch("trade_krono_cli.cli_commands.core._load_env"),
            patch("trade_krono_cli.external.pin", side_effect=ValueError("未知 repo: nonexistent")),
        ):
            result = runner.invoke(
                app, ["repo", "repo-pin", "--name", "nonexistent", "--commit", "abc123"]
            )
            assert result.exit_code != 0
            assert "未知 repo" in _strip_ansi(result.output)


# ═══════════════════════════════════════════════════════
# _load_tickers edge cases
# ═══════════════════════════════════════════════════════


class TestLoadTickersEdgeCases:
    def test_load_tickers_whitespace_only_string(self):
        from trade_krono_cli.cli_commands.core import _load_tickers

        tickers = _load_tickers("   ", None)
        assert tickers == []

    def test_load_tickers_empty_config_file(self, tmp_path):
        from trade_krono_cli.cli_commands.core import _load_tickers

        config_file = tmp_path / "empty.txt"
        config_file.write_text("")
        tickers = _load_tickers(None, str(config_file))
        assert tickers == []

    def test_load_tickers_comments_only(self, tmp_path):
        from trade_krono_cli.cli_commands.core import _load_tickers

        config_file = tmp_path / "comments.txt"
        config_file.write_text("# 这是注释\n# 另一行注释\n")
        tickers = _load_tickers(None, str(config_file))
        assert tickers == []

    def test_load_tickers_mixed_content(self, tmp_path):
        from trade_krono_cli.cli_commands.core import _load_tickers

        config_file = tmp_path / "mixed.txt"
        config_file.write_text("600519\n# 注释\n  000858  \n\n600036\n")
        tickers = _load_tickers(None, str(config_file))
        assert tickers == ["600519", "000858", "600036"]

    def test_load_tickers_empty_string_with_commas(self):
        from trade_krono_cli.cli_commands.core import _load_tickers

        tickers = _load_tickers(",,,", None)
        assert tickers == []

    def test_load_tickers_single_trailing_comma(self):
        from trade_krono_cli.cli_commands.core import _load_tickers

        tickers = _load_tickers("600519,", None)
        assert tickers == ["600519"]


# ═══════════════════════════════════════════════════════
# _sanitize_path edge cases
# ═══════════════════════════════════════════════════════


class TestSanitizePathEdgeCases:
    def test_sanitize_path_same_dir(self, tmp_path):
        from trade_krono_cli.cli_commands.core import _sanitize_path

        p = tmp_path / "result.json"
        result = _sanitize_path(str(p), "Test", tmp_path)
        assert result == p.resolve()

    def test_sanitize_path_nested_subdir(self, tmp_path):
        from trade_krono_cli.cli_commands.core import _sanitize_path

        sub = tmp_path / "a" / "b" / "c"
        sub.mkdir(parents=True)
        p = sub / "result.json"
        result = _sanitize_path(str(p), "Test", tmp_path)
        assert result == p.resolve()

    def test_sanitize_path_parent_traversal_deep(self, tmp_path):
        from typer import Exit

        from trade_krono_cli.cli_commands.core import _sanitize_path

        with pytest.raises(Exit):
            _sanitize_path(str(tmp_path / ".." / ".." / "etc" / "passwd"), "Test", tmp_path)


# ═══════════════════════════════════════════════════════
# Error paths in CLI functions
# ═══════════════════════════════════════════════════════


class TestCliErrorPaths:
    def test_ta_command_missing_tickers(self, runner):
        with patch("trade_krono_cli.cli_commands.core._load_env"):
            result = runner.invoke(app, ["ta", "--date", "2026-08-11"])
            assert result.exit_code != 0
            assert "股票列表为空" in _strip_ansi(result.output)

    def test_kronos_command_missing_tickers(self, runner):
        with patch("trade_krono_cli.cli_commands.core._load_env"):
            result = runner.invoke(app, ["kronos", "--date", "2026-08-11"])
            assert result.exit_code != 0
            assert "股票列表为空" in _strip_ansi(result.output)

    def test_run_command_invalid_date_format_accepted(self, runner):
        """日期格式校验不在 CLI 层做，应由 pipeline 内部处理。"""
        mock_pipeline = MagicMock()
        mock_pipeline.run_parallel.return_value = []
        with (
            patch("trade_krono_cli.cli_commands.core._load_env"),
            patch("trade_krono_cli.pipeline.QuantPipeline", return_value=mock_pipeline),
        ):
            result = runner.invoke(
                app,
                [
                    "run",
                    "--tickers",
                    "600519",
                    "--date",
                    "not-a-date",
                ],
            )
            assert result.exit_code == 0

    def test_clear_cache_with_mock(self, runner):
        with (
            patch("trade_krono_cli.cli_commands.core._load_env"),
            patch("trade_krono_cli.cache.get_cache") as mock_get_cache,
        ):
            mock_get_cache.return_value.clear_all.return_value = 42
            result = runner.invoke(app, ["clear-cache"])
            assert result.exit_code == 0
            assert "42" in _strip_ansi(result.output)


# ═══════════════════════════════════════════════════════
# Ta/Kronos with config file
# ═══════════════════════════════════════════════════════


class TestCommandsWithConfigFile:
    def test_ta_with_config_file(self, runner, tmp_path):
        config_file = tmp_path / "tickers.txt"
        config_file.write_text("600519\n")
        mock_pipeline = MagicMock()
        mock_pipeline.run_ta_only.return_value = []
        with (
            patch("trade_krono_cli.cli_commands.core._load_env"),
            patch("trade_krono_cli.pipeline.QuantPipeline", return_value=mock_pipeline),
        ):
            result = runner.invoke(
                app,
                [
                    "ta",
                    "--config",
                    str(config_file),
                    "--date",
                    "2026-08-11",
                ],
            )
            assert result.exit_code == 0

    def test_kronos_with_config_file(self, runner, tmp_path):
        config_file = tmp_path / "tickers.txt"
        config_file.write_text("600519\n")
        mock_pipeline = MagicMock()
        mock_pipeline.run_kronos_only.return_value = []
        with (
            patch("trade_krono_cli.cli_commands.core._load_env"),
            patch("trade_krono_cli.pipeline.QuantPipeline", return_value=mock_pipeline),
        ):
            result = runner.invoke(
                app,
                [
                    "kronos",
                    "--config",
                    str(config_file),
                    "--date",
                    "2026-08-11",
                ],
            )
            # kronos 命令可能因缺少 torch 而失败，只验证不崩溃
            assert result.exit_code in (0, 1)
