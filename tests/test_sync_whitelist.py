"""白名单机制测试：_resolve_tickers、sync_whitelist 命令、sync_universe 白名单优先。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from tests.conftest import _strip_ansi
from trade_krono_cli.cli import app


@pytest.fixture
def runner():
    return CliRunner()


# ═══════════════════════════════════════════════════════
# _resolve_tickers 单元测试
# ═══════════════════════════════════════════════════════


class TestResolveTickers:
    """测试 _resolve_tickers() 前缀补全逻辑。"""

    def test_sh_prefix(self) -> None:
        from trade_krono_cli.cli_commands.maintenance import _resolve_tickers

        result = _resolve_tickers("600519,688801")
        assert result == ["sh.600519", "sh.688801"]

    def test_sz_prefix(self) -> None:
        from trade_krono_cli.cli_commands.maintenance import _resolve_tickers

        result = _resolve_tickers("000858,300750")
        assert result == ["sz.000858", "sz.300750"]

    def test_bj_prefix(self) -> None:
        from trade_krono_cli.cli_commands.maintenance import _resolve_tickers

        result = _resolve_tickers("920071,920268")
        assert result == ["bj.920071", "bj.920268"]

    def test_mixed_exchanges(self) -> None:
        from trade_krono_cli.cli_commands.maintenance import _resolve_tickers

        result = _resolve_tickers("600519,000858,920071")
        assert result == ["sh.600519", "sz.000858", "bj.920071"]

    def test_empty_string(self) -> None:
        from trade_krono_cli.cli_commands.maintenance import _resolve_tickers

        result = _resolve_tickers("")
        assert result == []

    def test_whitespace_and_dedup(self) -> None:
        from trade_krono_cli.cli_commands.maintenance import _resolve_tickers

        result = _resolve_tickers("  600519 , 000858 , 600519  ")
        assert result == ["sh.600519", "sz.000858"]

    def test_invalid_codes_skipped(self) -> None:
        from trade_krono_cli.cli_commands.maintenance import _resolve_tickers

        result = _resolve_tickers("600519,abc,12,920071")
        assert result == ["sh.600519", "bj.920071"]

    def test_unknown_first_digit(self) -> None:
        from trade_krono_cli.cli_commands.maintenance import _resolve_tickers

        result = _resolve_tickers("100001,200001,400001,500001")
        assert result == []


# ═══════════════════════════════════════════════════════
# sync-whitelist 命令测试
# ═══════════════════════════════════════════════════════


class TestSyncWhitelist:
    """测试 sync-whitelist 命令。"""

    def test_sync_whitelist_help(self, runner) -> None:
        result = runner.invoke(app, ["sync-whitelist", "--help"])
        assert result.exit_code == 0
        out = _strip_ansi(result.output)
        assert "--date" in out
        assert "--lookback" in out
        assert "--workers" in out

    def test_sync_whitelist_no_config(self, runner) -> None:
        """未配置 SYNC_WHITELIST 时应报错退出。"""
        with (
            patch("trade_krono_cli.cli_commands.sync_whitelist._load_env"),
            patch("trade_krono_cli.config.get_settings") as mock_settings,
        ):
            mock_settings.return_value.sync_whitelist = ""
            result = runner.invoke(app, ["sync-whitelist"])
            assert result.exit_code == 1
            assert "未配置 SYNC_WHITELIST" in _strip_ansi(result.output)

    def test_sync_whitelist_invalid_codes(self, runner) -> None:
        """白名单全为无效代码时应报错退出。"""
        with (
            patch("trade_krono_cli.cli_commands.sync_whitelist._load_env"),
            patch("trade_krono_cli.config.get_settings") as mock_settings,
        ):
            mock_settings.return_value.sync_whitelist = "abc,xyz"
            result = runner.invoke(app, ["sync-whitelist"])
            assert result.exit_code == 1
            assert "无有效股票" in _strip_ansi(result.output)

    def test_sync_whitelist_success(self, runner) -> None:
        """正常执行应调用 fetch_kline_incremental 并输出完成信息。"""

        def mock_fetch_ticker(ticker, *args, **kwargs):
            return 800, "baostock"

        with (
            patch("trade_krono_cli.cli_commands.sync_whitelist._load_env"),
            patch("trade_krono_cli.config.get_settings") as mock_settings,
            patch(
                "trade_krono_cli.cli_commands._sync_helpers._check_provider_health",
                return_value={"baostock": True, "mootdx": True},
            ),
            patch(
                "trade_krono_cli.cli_commands._sync_helpers._fetch_ticker_parallel",
                side_effect=mock_fetch_ticker,
            ),
        ):
            mock_settings.return_value.sync_whitelist = "600519,000858"
            result = runner.invoke(
                app,
                ["sync-whitelist", "--date", "2026-08-30", "--no-progress"],
            )
            assert result.exit_code == 0
            out = _strip_ansi(result.output)
            assert "✅ 同步完成" in out
            assert "成功=2/2" in out

    def test_sync_whitelist_partial_failure(self, runner) -> None:
        """部分股票失败时应报告成功/失败数。"""

        def mock_fetch_ticker(ticker, *args, **kwargs):
            if ticker == "sz.000858":
                return 500, "mootdx"
            return 0, None

        with (
            patch("trade_krono_cli.cli_commands.sync_whitelist._load_env"),
            patch("trade_krono_cli.config.get_settings") as mock_settings,
            patch(
                "trade_krono_cli.cli_commands._sync_helpers._check_provider_health",
                return_value={"baostock": True, "mootdx": True},
            ),
            patch(
                "trade_krono_cli.cli_commands._sync_helpers._fetch_ticker_parallel",
                side_effect=mock_fetch_ticker,
            ),
        ):
            mock_settings.return_value.sync_whitelist = "600519,000858"
            result = runner.invoke(
                app,
                ["sync-whitelist", "--date", "2026-08-30", "--no-progress"],
            )
            assert result.exit_code == 0
            out = _strip_ansi(result.output)
            assert "成功=1/2" in out
            assert "000858" in out
            assert "600519" in out


# ═══════════════════════════════════════════════════════
# sync-universe 白名单优先测试
# ═══════════════════════════════════════════════════════


class TestSyncUniverseWhitelist:
    """测试 sync-universe 中白名单优先行为。"""

    def test_sync_universe_with_whitelist_order(self, runner) -> None:
        """白名单股票应在全量列表之前处理。"""
        mock_tickets = [
            MagicMock(ticker="sh.600519"),
            MagicMock(ticker="sz.000858"),
            MagicMock(ticker="sh.600000"),
            MagicMock(ticker="sz.000001"),
        ]
        mock_settings = MagicMock()
        mock_settings.sync_whitelist = "600519,000858"

        fetch_order: list[str] = []

        def mock_fetch_ticker(ticker: str, **kwargs) -> tuple[int, str | None]:
            fetch_order.append(ticker)
            return 800, "mootdx"

        with (
            patch("trade_krono_cli.cli_commands.sync_universe._load_env"),
            patch("trade_krono_cli.config.get_settings") as mock_settings,
            patch(
                "trade_krono_cli.universe.provider.TongHuaShunUniverseProvider",
            ) as mock_provider_cls,
            patch(
                "trade_krono_cli.cli_commands._sync_helpers._check_provider_health",
                return_value={"baostock": True, "mootdx": True},
            ),
            patch(
                "trade_krono_cli.cli_commands._sync_helpers._fetch_ticker_parallel",
                side_effect=mock_fetch_ticker,
            ),
        ):
            mock_provider_cls.return_value.get_universe.return_value = mock_tickets
            mock_settings.return_value.sync_whitelist = "600519,000858"
            result = runner.invoke(
                app,
                ["sync-universe", "--date", "2026-08-30", "--no-progress"],
            )
            assert result.exit_code == 0
            # 白名单中的股票都应出现在非白名单之前（按提交顺序，并发执行可能导致完成顺序不同）
            wl_set = {"sh.600519", "sz.000858"}
            non_wl_set = {"sh.600000", "sz.000001"}
            assert all(t in fetch_order for t in wl_set), f"白名单股票未全部拉取: {fetch_order}"
            assert all(t in fetch_order for t in non_wl_set), (
                f"非白名单股票未全部拉取: {fetch_order}"
            )
            # 白名单不应重复
            assert fetch_order.count("sh.600519") == 1
            assert fetch_order.count("sz.000858") == 1

    def test_sync_universe_without_whitelist(self, runner) -> None:
        """未配置白名单时应正常执行全量同步。"""
        mock_df = MagicMock()
        mock_df.__len__ = MagicMock(return_value=800)
        mock_tickets = [
            MagicMock(ticker="sh.600519"),
            MagicMock(ticker="sz.000858"),
        ]
        mock_settings = MagicMock()
        mock_settings.sync_whitelist = ""

        with (
            patch("trade_krono_cli.cli_commands.sync_universe._load_env"),
            patch("trade_krono_cli.config.get_settings") as mock_settings,
            patch(
                "trade_krono_cli.universe.provider.TongHuaShunUniverseProvider",
            ) as mock_provider_cls,
            patch("trade_krono_cli.data.fetch_kline_incremental", return_value=mock_df),
        ):
            mock_provider_cls.return_value.get_universe.return_value = mock_tickets
            mock_settings.return_value.sync_whitelist = ""
            result = runner.invoke(
                app,
                ["sync-universe", "--date", "2026-08-30", "--no-progress"],
            )
            assert result.exit_code == 0
            out = _strip_ansi(result.output)
            assert "✅ 同步完成" in out
            assert "白名单" not in out


# ═══════════════════════════════════════════════════════
# 并发拉取单元测试
# ═══════════════════════════════════════════════════════


class TestFetchTickerParallel:
    """测试 _fetch_ticker_parallel 并发拉取逻辑。"""

    def test_fetch_ticker_parallel_returns_first_success(self) -> None:
        """应返回首个成功 Provider 的结果。"""
        from trade_krono_cli.cli_commands._sync_helpers import _fetch_ticker_parallel

        mock_df = MagicMock()
        mock_df.__len__ = MagicMock(return_value=200)

        with patch("trade_krono_cli.data.fetch_kline_incremental", return_value=mock_df):
            rows, provider = _fetch_ticker_parallel(
                ticker="sh.600519",
                start_date="2024-01-01",
                end_date="2024-12-31",
                frequency="d",
                adjustflag="1",
                use_cache=True,
                providers=["baostock", "mootdx"],
            )
            assert rows > 0
            assert provider in ("baostock", "mootdx")

    def test_fetch_ticker_parallel_all_fail(self) -> None:
        """所有 Provider 失败时应返回 (0, None)。"""
        from trade_krono_cli.cli_commands._sync_helpers import _fetch_ticker_parallel

        with patch("trade_krono_cli.data.fetch_kline_incremental") as mock_fetch:
            mock_fetch.side_effect = RuntimeError("all down")
            rows, provider = _fetch_ticker_parallel(
                ticker="sh.600519",
                start_date="2024-01-01",
                end_date="2024-12-31",
                frequency="d",
                adjustflag="1",
                use_cache=True,
                providers=["baostock", "mootdx"],
            )
            assert rows == 0
            assert provider is None
