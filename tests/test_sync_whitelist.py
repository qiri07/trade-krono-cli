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

    def test_sh_prefix(self):
        from trade_krono_cli.cli_commands.maintenance import _resolve_tickers

        result = _resolve_tickers("600519,688801")
        assert result == ["sh.600519", "sh.688801"]

    def test_sz_prefix(self):
        from trade_krono_cli.cli_commands.maintenance import _resolve_tickers

        result = _resolve_tickers("000858,300750")
        assert result == ["sz.000858", "sz.300750"]

    def test_bj_prefix(self):
        from trade_krono_cli.cli_commands.maintenance import _resolve_tickers

        result = _resolve_tickers("920071,920268")
        assert result == ["bj.920071", "bj.920268"]

    def test_mixed_exchanges(self):
        from trade_krono_cli.cli_commands.maintenance import _resolve_tickers

        result = _resolve_tickers("600519,000858,920071")
        assert result == ["sh.600519", "sz.000858", "bj.920071"]

    def test_empty_string(self):
        from trade_krono_cli.cli_commands.maintenance import _resolve_tickers

        result = _resolve_tickers("")
        assert result == []

    def test_whitespace_and_dedup(self):
        from trade_krono_cli.cli_commands.maintenance import _resolve_tickers

        result = _resolve_tickers("  600519 , 000858 , 600519  ")
        assert result == ["sh.600519", "sz.000858"]

    def test_invalid_codes_skipped(self):
        from trade_krono_cli.cli_commands.maintenance import _resolve_tickers

        result = _resolve_tickers("600519,abc,12,920071")
        assert result == ["sh.600519", "bj.920071"]

    def test_unknown_first_digit(self):
        from trade_krono_cli.cli_commands.maintenance import _resolve_tickers

        result = _resolve_tickers("100001,200001,400001,500001")
        assert result == []


# ═══════════════════════════════════════════════════════
# sync-whitelist 命令测试
# ═══════════════════════════════════════════════════════


class TestSyncWhitelist:
    """测试 sync-whitelist 命令。"""

    def test_sync_whitelist_help(self, runner):
        result = runner.invoke(app, ["sync-whitelist", "--help"])
        assert result.exit_code == 0
        out = _strip_ansi(result.output)
        assert "--date" in out
        assert "--lookback" in out
        assert "--delay" in out

    def test_sync_whitelist_no_config(self, runner):
        """未配置 SYNC_WHITELIST 时应报错退出。"""
        with (
            patch("trade_krono_cli.cli_commands.maintenance._load_env"),
            patch("trade_krono_cli.config.get_settings") as mock_settings,
        ):
            mock_settings.return_value.sync_whitelist = ""
            result = runner.invoke(app, ["sync-whitelist"])
            assert result.exit_code == 1
            assert "未配置 SYNC_WHITELIST" in _strip_ansi(result.output)

    def test_sync_whitelist_invalid_codes(self, runner):
        """白名单全为无效代码时应报错退出。"""
        with (
            patch("trade_krono_cli.cli_commands.maintenance._load_env"),
            patch("trade_krono_cli.config.get_settings") as mock_settings,
        ):
            mock_settings.return_value.sync_whitelist = "abc,xyz"
            result = runner.invoke(app, ["sync-whitelist"])
            assert result.exit_code == 1
            assert "无有效股票" in _strip_ansi(result.output)

    def test_sync_whitelist_success(self, runner):
        """正常执行应调用 fetch_kline_incremental 并输出完成信息。"""
        mock_df = MagicMock()
        mock_df.__len__ = MagicMock(return_value=800)

        with (
            patch("trade_krono_cli.cli_commands.maintenance._load_env"),
            patch("trade_krono_cli.config.get_settings") as mock_settings,
            patch(
                "trade_krono_cli.data.fetch_kline_incremental", return_value=mock_df
            ) as mock_fetch,
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
            # 验证前缀已补全
            called_tickers = {c[1]["ticker"] for c in mock_fetch.call_args_list}
            assert called_tickers == {"sh.600519", "sz.000858"}

    def test_sync_whitelist_partial_failure(self, runner):
        """部分股票失败时应报告成功/失败数。"""
        mock_df = MagicMock()
        mock_df.__len__ = MagicMock(return_value=500)

        def side_effect(**kwargs):
            ticker = kwargs.get("ticker", "")
            if ticker == "sh.600519":
                raise RuntimeError("network error")
            return mock_df

        with (
            patch("trade_krono_cli.cli_commands.maintenance._load_env"),
            patch("trade_krono_cli.config.get_settings") as mock_settings,
            patch("trade_krono_cli.data.fetch_kline_incremental", side_effect=side_effect),
        ):
            mock_settings.return_value.sync_whitelist = "600519,000858"
            result = runner.invoke(
                app,
                ["sync-whitelist", "--date", "2026-08-30", "--no-progress"],
            )
            assert result.exit_code == 0
            out = _strip_ansi(result.output)
            assert "成功=1/2" in out
            assert "000858" in out  # 成功股票名
            assert "600519" in out  # 失败股票名


# ═══════════════════════════════════════════════════════
# sync-universe 白名单优先测试
# ═══════════════════════════════════════════════════════


class TestSyncUniverseWhitelist:
    """测试 sync-universe 中白名单优先行为。"""

    def test_sync_universe_with_whitelist_order(self, runner):
        """白名单股票应在全量列表之前处理。"""
        mock_df = MagicMock()
        mock_df.__len__ = MagicMock(return_value=800)
        mock_tickets = [
            MagicMock(ticker="sh.600519"),
            MagicMock(ticker="sz.000858"),
            MagicMock(ticker="sh.600000"),
            MagicMock(ticker="sz.000001"),
        ]

        call_order: list[str] = []

        def record_fetch(**kwargs):
            call_order.append(kwargs.get("ticker", ""))
            return mock_df

        with (
            patch("trade_krono_cli.cli_commands.maintenance._load_env"),
            patch("trade_krono_cli.config.get_settings") as mock_settings,
            patch(
                "trade_krono_cli.universe.provider.TongHuaShunUniverseProvider"
            ) as mock_provider_cls,
            patch("trade_krono_cli.data.fetch_kline_incremental", side_effect=record_fetch),
        ):
            mock_provider_cls.return_value.get_universe.return_value = mock_tickets
            mock_settings.return_value.sync_whitelist = "600519,000858"
            result = runner.invoke(
                app,
                ["sync-universe", "--date", "2026-08-30", "--no-progress"],
            )
            assert result.exit_code == 0
            # 白名单应先于非白名单
            wl_idx = [i for i, t in enumerate(call_order) if t in ("sh.600519", "sz.000858")]
            non_wl_idx = [
                i for i, t in enumerate(call_order) if t not in ("sh.600519", "sz.000858")
            ]
            assert max(wl_idx) < min(non_wl_idx), f"白名单未优先: order={call_order}"
            # 白名单不应重复
            assert call_order.count("sh.600519") == 1
            assert call_order.count("sz.000858") == 1

    def test_sync_universe_without_whitelist(self, runner):
        """未配置白名单时应正常执行全量同步。"""
        mock_df = MagicMock()
        mock_df.__len__ = MagicMock(return_value=800)
        mock_tickets = [
            MagicMock(ticker="sh.600519"),
            MagicMock(ticker="sz.000858"),
        ]

        with (
            patch("trade_krono_cli.cli_commands.maintenance._load_env"),
            patch("trade_krono_cli.config.get_settings") as mock_settings,
            patch(
                "trade_krono_cli.universe.provider.TongHuaShunUniverseProvider"
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
