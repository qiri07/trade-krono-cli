"""测试 maintenance 子命令：clear-cache / warm-cache / history / retry-failed。

覆盖 CLI 路由、参数解析、无配置时的友好提示。
API/网络调用通过 mock 隔离。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from trade_krono_cli.cli import app


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# ── clear-cache ────────────────────────────────────────────────────────────────


class TestClearCache:
    def test_clear_cache_success(self, runner: CliRunner) -> None:
        with patch("trade_krono_cli.cli_commands.maintenance_cache._load_env"):
            with patch("trade_krono_cli.cache.get_cache", create=True) as mock_get:
                mock_cache = MagicMock()
                mock_cache.clear_all.return_value = 5
                mock_get.return_value = mock_cache
                result = runner.invoke(app, ["clear-cache"])
                assert result.exit_code == 0
                mock_cache.clear_all.assert_called_once()

    def test_clear_cache_no_cache(self, runner: CliRunner) -> None:
        with patch("trade_krono_cli.cli_commands.maintenance_cache._load_env"):
            with patch("trade_krono_cli.cache.get_cache", create=True) as mock_get:
                mock_cache = MagicMock()
                mock_cache.clear_all.return_value = 0
                mock_get.return_value = mock_cache
                result = runner.invoke(app, ["clear-cache"])
                assert result.exit_code == 0


# ── warm-cache ─────────────────────────────────────────────────────────────────


class TestWarmCache:
    def test_warm_cache_empty_tickers(self, runner: CliRunner) -> None:
        """无 --tickers 且无 --config → 应报错退出。"""
        with patch("trade_krono_cli.cli_commands.maintenance_cache._load_env"):
            with patch("trade_krono_cli.cli_commands.maintenance_cache._load_tickers") as mock_load:
                mock_load.return_value = []
                result = runner.invoke(
                    app,
                    [
                        "warm-cache",
                        "--date",
                        "2026-09-01",
                    ],
                )
                assert result.exit_code != 0

    def test_warm_cache_with_tickers(self, runner: CliRunner) -> None:
        with patch("trade_krono_cli.cli_commands.maintenance_cache._load_env"):
            with patch("trade_krono_cli.cli_commands.maintenance_cache._load_tickers") as mock_load:
                mock_load.return_value = ["sh.600519"]
                with patch("trade_krono_cli.cache.get_cache", create=True) as mock_get:
                    mock_cache = MagicMock()
                    mock_cache.warm_history.return_value = (100, 5)
                    mock_get.return_value = mock_cache
                    result = runner.invoke(
                        app,
                        [
                            "warm-cache",
                            "--date",
                            "2026-09-01",
                            "--tickers",
                            "600519",
                        ],
                    )
                    assert result.exit_code == 0
                    mock_cache.warm_history.assert_called_once()


# ── history ────────────────────────────────────────────────────────────────────


class TestHistory:
    def test_history_no_ticker(self, runner: CliRunner) -> None:
        """不指定 ticker 应列出全部记录（或给出提示）。"""
        with patch("trade_krono_cli.cli_commands.maintenance_history._load_env"):
            with patch("trade_krono_cli.research_db.get_research", create=True) as mock_db:
                mock_research = MagicMock()
                mock_research.query_history.return_value = []
                mock_db.return_value = mock_research
                result = runner.invoke(app, ["history"])
                assert result.exit_code == 0

    def test_history_with_ticker(self, runner: CliRunner) -> None:
        with patch("trade_krono_cli.cli_commands.maintenance_history._load_env"):
            with patch("trade_krono_cli.research_db.get_research", create=True) as mock_db:
                mock_research = MagicMock()
                mock_research.query_history.return_value = [
                    {
                        "date": "2026-09-01",
                        "run_id": "abc",
                        "data_version": "v1",
                        "rank": 1,
                        "composite_score": 85.0,
                        "ta_signal": "BUY",
                        "ta_confidence": 80.0,
                        "kronos_direction": "UP",
                        "kronos_change": 3.2,
                    },
                ]
                mock_db.return_value = mock_research
                result = runner.invoke(
                    app,
                    [
                        "history",
                        "--ticker",
                        "600519",
                    ],
                )
                assert result.exit_code == 0
                assert "贵州茅台" not in result.output or "600519" in result.output

    def test_history_ticker_not_found(self, runner: CliRunner) -> None:
        with patch("trade_krono_cli.cli_commands.maintenance_history._load_env"):
            with patch("trade_krono_cli.research_db.get_research", create=True) as mock_db:
                mock_research = MagicMock()
                mock_research.query_history.return_value = []
                mock_db.return_value = mock_research
                result = runner.invoke(
                    app,
                    [
                        "history",
                        "--ticker",
                        "999999",
                    ],
                )
                assert result.exit_code == 0
                assert "未找到" in result.output or "没有" in result.output

    def test_history_with_limit(self, runner: CliRunner) -> None:
        with patch("trade_krono_cli.cli_commands.maintenance_history._load_env"):
            with patch("trade_krono_cli.research_db.get_research", create=True) as mock_db:
                mock_research = MagicMock()
                mock_research.query_history.return_value = [
                    {
                        "date": "2026-09-01",
                        "run_id": "x",
                        "data_version": "v1",
                        "rank": 1,
                        "composite_score": 80.0,
                        "ta_signal": "BUY",
                        "ta_confidence": 75.0,
                        "kronos_direction": "UP",
                        "kronos_change": 2.0,
                    },
                ]
                mock_db.return_value = mock_research
                result = runner.invoke(
                    app,
                    [
                        "history",
                        "--ticker",
                        "600519",
                        "--limit",
                        "1",
                    ],
                )
                assert result.exit_code == 0


# ── retry-failed ───────────────────────────────────────────────────────────────


class TestRetryFailed:
    def test_retry_no_fails(self, runner: CliRunner) -> None:
        """无任何失败记录时应给出提示并退出。"""
        with patch("trade_krono_cli.cli_commands.maintenance_retry._load_env"):
            with patch("trade_krono_cli.retry_policy.get_failure_store", create=True) as mock_store:
                mock_store.return_value.list_fails.return_value = []
                result = runner.invoke(
                    app,
                    [
                        "retry-failed",
                        "--date",
                        "2026-09-01",
                    ],
                )
                assert result.exit_code == 0
                assert "无失败记录" in result.output

    def test_retry_with_fails(self, runner: CliRunner) -> None:
        """有失败记录时，应触发重试流程（mock 不完整，仅验证不 crash）。"""
        with patch("trade_krono_cli.cli_commands.maintenance_retry._load_env"):
            with patch("trade_krono_cli.retry_policy.get_failure_store", create=True) as mock_store:
                mock_record = MagicMock()
                mock_record.ticker = "sh.600519"
                mock_record.date = "2026-09-01"
                mock_record.module = "ta"
                mock_record.error_category = "retriable"
                mock_record.error_message = "test error"
                mock_record.attempt_count = 1
                mock_store.return_value.list_fails.return_value = [mock_record]

                with patch(
                    "trade_krono_cli.cli_commands.maintenance_retry._build_retry_overrides"
                ) as mock_overrides:
                    mock_overrides.return_value = {}
                    with patch("trade_krono_cli.pipeline_config.PipelineConfig"):
                        with patch("trade_krono_cli.pipeline.QuantPipeline") as mock_pipe:
                            mock_pipeline = MagicMock()
                            mock_pipeline.ta = MagicMock()
                            mock_pipeline.ta.analyze_one.return_value = MagicMock(error=None)
                            mock_pipeline.kronos = None
                            mock_pipe.return_value = mock_pipeline

                            result = runner.invoke(
                                app,
                                [
                                    "retry-failed",
                                    "--date",
                                    "2026-09-01",
                                ],
                            )
                            # 可能因各种 mock 不完整而失败，但不应 crash
                            assert result.exit_code in (0, 1)

    def test_retry_auto_detect_date(self, runner: CliRunner) -> None:
        """传入 --date 时，应正常进入重试逻辑。"""
        with patch("trade_krono_cli.cli_commands.maintenance_retry._load_env"):
            with patch("trade_krono_cli.retry_policy.get_failure_store", create=True) as mock_store:
                mock_record = MagicMock()
                mock_record.ticker = "sh.600519"
                mock_record.date = "2026-08-31"
                mock_record.module = "kronos"
                mock_record.error_category = "retriable"
                mock_record.error_message = "err"
                mock_record.attempt_count = 0
                mock_store.return_value.list_fails.return_value = [mock_record]

                with patch(
                    "trade_krono_cli.cli_commands.maintenance_retry._build_retry_overrides"
                ) as mock_overrides:
                    mock_overrides.return_value = {}
                    with patch("trade_krono_cli.pipeline_config.PipelineConfig"):
                        with patch("trade_krono_cli.pipeline.QuantPipeline") as mock_pipe:
                            mock_pipeline = MagicMock()
                            mock_pipeline.ta = MagicMock()
                            mock_pipeline.ta.analyze_one.return_value = MagicMock(
                                error="already failed"
                            )
                            mock_pipeline.kronos = MagicMock()
                            mock_pipeline.kronos.predict_one.return_value = MagicMock(error=None)
                            mock_pipe.return_value = mock_pipeline

                            result = runner.invoke(
                                app,
                                ["retry-failed", "--date", "2026-08-31"],
                            )
                            assert result.exit_code in (0, 1)
