"""边界条件与异常场景测试：无效股票代码、数据缺失、空配置、外部仓库异常等。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from typer import Exit
from typer.testing import CliRunner

from tests.conftest import _strip_ansi
from trade_krono_cli.cli import app
from trade_krono_cli.cli_commands.core import _load_tickers, _sanitize_path


@pytest.fixture
def runner():
    return CliRunner()


# ═══════════════════════════════════════════════════════
# Invalid ticker handling
# ═══════════════════════════════════════════════════════


class TestInvalidTickers:
    def test_load_tickers_empty_list_from_string(self):
        assert _load_tickers("", None) == []

    def test_load_tickers_only_whitespace(self):
        assert _load_tickers("   \n  \n  ", None) == []

    def test_ta_command_with_empty_tickers_string(self, runner):
        with patch("trade_krono_cli.cli_commands.core._load_env"):
            result = runner.invoke(app, ["ta", "--tickers", "", "--date", "2026-08-11"])
            assert result.exit_code != 0

    def test_kronos_command_with_empty_tickers_string(self, runner):
        with patch("trade_krono_cli.cli_commands.core._load_env"):
            result = runner.invoke(app, ["kronos", "--tickers", "", "--date", "2026-08-11"])
            assert result.exit_code != 0

    def test_run_command_with_only_comments_config(self, runner, tmp_path):
        config_file = tmp_path / "only_comments.txt"
        config_file.write_text("# 只有注释\n# 第二行注释\n")
        with patch("trade_krono_cli.cli_commands.core._load_env"):
            result = runner.invoke(
                app,
                [
                    "run",
                    "--stock-file",
                    str(config_file),
                    "--date",
                    "2026-08-11",
                ],
            )
            assert result.exit_code != 0
            assert "股票列表为空" in _strip_ansi(result.output)


# ═══════════════════════════════════════════════════════
# Empty / missing data scenarios
# ═══════════════════════════════════════════════════════


class TestMissingData:
    def test_merge_results_empty_pool(self):
        """空股票池合并应返回空列表。"""
        from trade_krono_cli.pipeline.merge import merge_results

        result = merge_results([], [])
        assert result == []

    def test_merge_results_all_failed(self):
        """所有模块都失败时应返回空结果。"""
        from trade_krono_cli.kronos_runner import KronosForecastResult
        from trade_krono_cli.pipeline.merge import merge_results
        from trade_krono_cli.ta_runner import StockAnalysisResult

        ta_results = [StockAnalysisResult(ticker="sh.600519", date="2026-08-11", error="fail")]
        kronos_results = [
            KronosForecastResult(
                ticker="sh.600519",
                eval_date="2026-08-11",
                horizon=30,
                predicted_close_mean=100.0,
                error="fail",
            )
        ]
        # 当两边都有 error 时，merge 仍可能返回带 error 标记的条目
        result = merge_results(ta_results, kronos_results)
        # 验证不崩溃，结果为列表
        assert isinstance(result, list)

    def test_research_db_empty_query(self, tmp_path):
        """空查询应返回空列表。"""
        from trade_krono_cli.research_db import ResearchDatabase

        db = ResearchDatabase(db_path=tmp_path / "empty.db")
        records = db.query_history("999999", limit=10)
        assert records == []

    def test_cache_get_missing_ta_key(self, tmp_path):
        """查询不存在的 TA 缓存 key 应返回 None。"""
        from trade_krono_cli.cache import Cache

        cache = Cache(db_path=tmp_path / "test_missing.db")
        assert cache.get_ta("sh.999999", "2026-08-11") is None

    def test_cache_get_missing_kronos_key(self, tmp_path):
        """查询不存在的 Kronos 缓存 key 应返回 None。"""
        from trade_krono_cli.cache import Cache

        cache = Cache(db_path=tmp_path / "test_missing_k.db")
        assert cache.get_kronos("sh.999999", "2026-08-11", 30) is None


# ═══════════════════════════════════════════════════════
# Empty config file
# ═══════════════════════════════════════════════════════


class TestEmptyConfig:
    def test_load_tickers_from_empty_file(self, tmp_path):
        config_file = tmp_path / "empty.txt"
        config_file.write_text("")
        tickers = _load_tickers(None, str(config_file))
        assert tickers == []

    def test_load_tickers_from_whitespace_file(self, tmp_path):
        config_file = tmp_path / "whitespace.txt"
        config_file.write_text("   \n  \n\n")
        tickers = _load_tickers(None, str(config_file))
        assert tickers == []

    def test_pipeline_config_load_missing_file_raises(self):
        from trade_krono_cli.pipeline_config import PipelineConfig

        with pytest.raises((FileNotFoundError, OSError)):
            PipelineConfig.load("/nonexistent/path.yaml")

    def test_pipeline_config_load_invalid_yaml(self, tmp_path):
        from trade_krono_cli.pipeline_config import PipelineConfig

        bad_yaml = tmp_path / "bad.yaml"
        bad_yaml.write_text(":\n  - invalid yaml [[[\n")
        with pytest.raises(Exception):
            PipelineConfig.load(str(bad_yaml))


# ═══════════════════════════════════════════════════════
# External repo errors
# ═══════════════════════════════════════════════════════


class TestExternalRepoErrors:
    def test_repo_status_no_repos(self, runner):
        """无外部 repo 配置时应给出提示。"""
        with (
            patch("trade_krono_cli.cli_commands.core._load_env"),
            patch("trade_krono_cli.external.status", return_value=[]),
        ):
            result = runner.invoke(app, ["repo", "status"])
            # 无 repo 时可能有退出码 0 或 2（取决于是否有默认配置）
            assert "未检测到外部 repo" in _strip_ansi(result.output) or result.exit_code in (0, 2)

    def test_repo_doctor_no_entries_raises_exit(self, runner):
        """无 repo 配置时 doctor 应退出非 0。"""
        with (
            patch("trade_krono_cli.cli_commands.core._load_env"),
            patch("trade_krono_cli.external.doctor", return_value=[]),
            patch("trade_krono_cli.external.status", return_value=[]),
            patch("trade_krono_cli.external.load_lock", return_value={}),
        ):
            result = runner.invoke(app, ["repo", "repo-doctor"])
            assert result.exit_code != 0
            assert "未检测到外部 repo" in result.output

    def test_repo_update_all_pinned(self, runner):
        """全部 pinned 时 update 应提示跳过。"""
        from trade_krono_cli.external import ExternalRepo

        pinned_repo = ExternalRepo(
            name="tradingagents",
            path="/tmp/ta",
            branch="main",
            url="",
            commit="abc123",
        )
        with (
            patch("trade_krono_cli.cli_commands.core._load_env"),
            patch("trade_krono_cli.external.get_repos", return_value=[pinned_repo]),
            patch("trade_krono_cli.external.update", return_value={}),
        ):
            result = runner.invoke(app, ["repo", "repo-update"])
            assert result.exit_code == 0
            assert "已 pinned，跳过" in _strip_ansi(result.output)

    def test_repo_pin_nonexistent_repo(self, runner):
        with (
            patch("trade_krono_cli.cli_commands.core._load_env"),
            patch("trade_krono_cli.external.pin", side_effect=ValueError("未知 repo: fake")),
        ):
            result = runner.invoke(
                app, ["repo", "repo-pin", "--name", "fake", "--commit", "abc123"]
            )
            assert result.exit_code != 0
            assert "未知 repo" in _strip_ansi(result.output)


# ═══════════════════════════════════════════════════════
# Path traversal & symlink edge cases
# ═══════════════════════════════════════════════════════


class TestPathTraversalEdgeCases:
    def test_sanitize_path_with_double_dot(self, tmp_path):
        with pytest.raises(Exit):
            _sanitize_path(str(tmp_path / ".." / ".." / "etc" / "passwd"), "Test", tmp_path)

    def test_sanitize_path_symlink_chain_escape(self, tmp_path):
        link_a = tmp_path / "link_a"
        link_b = tmp_path / "link_b"
        link_a.symlink_to(link_b)
        link_b.symlink_to("/etc")
        with pytest.raises(Exit):
            _sanitize_path(str(link_a / "passwd"), "Test", tmp_path)


# ═══════════════════════════════════════════════════════
# Invalid date handling
# ═══════════════════════════════════════════════════════


class TestInvalidDate:
    def test_run_with_future_date(self, runner):
        """未来日期不应在 CLI 层拒绝。"""
        mock_pipeline = MagicMock()
        mock_pipeline.run_parallel.return_value = []
        with (
            patch("trade_krono_cli.cli_commands.core._load_env"),
            patch("trade_krono_cli.pipeline.QuantPipeline", return_value=mock_pipeline),
        ):
            result = runner.invoke(app, ["run", "--tickers", "600519", "--date", "2099-01-01"])
            assert result.exit_code == 0

    def test_ta_with_future_date(self, runner):
        mock_pipeline = MagicMock()
        mock_pipeline.run_ta_only.return_value = []
        with (
            patch("trade_krono_cli.cli_commands.core._load_env"),
            patch("trade_krono_cli.pipeline.QuantPipeline", return_value=mock_pipeline),
        ):
            result = runner.invoke(app, ["ta", "--tickers", "600519", "--date", "2099-01-01"])
            assert result.exit_code == 0

    def test_warm_cache_with_future_date(self, runner):
        mock_cache = MagicMock()
        mock_cache.warm_history.return_value = (10, 1)
        with (
            patch("trade_krono_cli.cli_commands.core._load_env"),
            patch("trade_krono_cli.cache.get_cache", return_value=mock_cache),
        ):
            result = runner.invoke(
                app, ["warm-cache", "--tickers", "600519", "--date", "2099-01-01"]
            )
            assert result.exit_code == 0


# ═══════════════════════════════════════════════════════
# Data provider fallback
# ═══════════════════════════════════════════════════════


class TestDataProviderFallback:
    def test_factory_get_providers_unknown_primary(self):
        """使用不存在的主 provider 名称时不应崩溃。"""
        from trade_krono_cli.data_providers.factory import DataProviderFactory

        factory = DataProviderFactory(primary="nonexistent_provider_xyz")
        # 不应抛出异常，可能返回可用 provider 或空列表
        providers = factory.get_providers()
        assert isinstance(providers, list)

    def test_factory_get_provider_by_name(self):
        """通过名称获取 provider。"""
        from trade_krono_cli.data_providers.factory import DataProviderFactory

        factory = DataProviderFactory()
        provider = factory.get_provider("baostock")
        # 不强制要求成功，只验证不崩溃
        assert provider is None or hasattr(provider, "fetch_kline")


# ═══════════════════════════════════════════════════════
# Config validation with empty/invalid values
# ═══════════════════════════════════════════════════════


class TestConfigValidation:
    def test_parse_range_invalid_format(self):
        from trade_krono_cli.pipeline_config import _parse_range

        assert _parse_range("abc") is None
        assert _parse_range("1") is None
        assert _parse_range("1,2,3") is None
        assert _parse_range("") is None
        assert _parse_range(None) is None

    def test_parse_comma_list_empty(self):
        from trade_krono_cli.pipeline_config import _parse_comma_list

        assert _parse_comma_list("") == []
        assert _parse_comma_list(None) == []
        assert _parse_comma_list("  ,  ,  ") == []

    def test_parse_float_invalid(self):
        from trade_krono_cli.pipeline_config import _parse_float

        assert _parse_float("abc") is None
        assert _parse_float("") is None
        assert _parse_float(None) is None

    def test_pipeline_config_from_dict_missing_fields(self):
        from trade_krono_cli.pipeline_config import PipelineConfig

        cfg = PipelineConfig.from_dict({})
        assert cfg is not None
        assert cfg.sample_count == 5
        assert cfg.min_confidence == 55.0


# ═══════════════════════════════════════════════════════
# Scoring with incomplete data
# ═══════════════════════════════════════════════════════


class TestScoringEdgeCases:
    def test_linear_scorer_with_none_values(self):
        from trade_krono_cli.scoring import LinearScorer

        s = LinearScorer()
        merged = {
            "ticker": "sh.600519",
            "ta_confidence": None,
            "kronos_change_pct": None,
            "kronos_direction": None,
            "risk_score_total": None,
            "kronos_prediction_uncertainty": None,
            "rank": None,
            "_pool_size": 1,
        }
        score = s.score(merged)
        assert isinstance(score, (int, float))

    def test_multiplicative_scorer_with_zero_risk(self):
        from trade_krono_cli.scoring import MultiplicativeScorer

        s = MultiplicativeScorer()
        merged = {
            "ticker": "sh.600519",
            "ta_confidence": 80.0,
            "kronos_change_pct": 5.0,
            "kronos_direction": "UP",
            "risk_score_total": 0.0,
            "kronos_prediction_uncertainty": {"confidence_score": 70.0},
            "rank": None,
            "_pool_size": 1,
        }
        score = s.score(merged)
        assert score > 0

    def test_rank_based_scorer_zero_pool_size(self):
        from trade_krono_cli.scoring import RankBasedScorer

        s = RankBasedScorer()
        merged = {
            "ticker": "sh.600519",
            "ta_confidence": 80.0,
            "kronos_change_pct": 5.0,
            "kronos_direction": "UP",
            "risk_score_total": 10.0,
            "kronos_prediction_uncertainty": None,
            "rank": 1,
            "_pool_size": 0,
        }
        score = s.score(merged)
        assert isinstance(score, (int, float))
        assert 0 <= score <= 100
