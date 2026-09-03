"""tests/test_rank_providers.py — rank_providers CLI 命令单元测试。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import typer

from trade_krono_cli.cli_commands.maintenance_sync import rank_providers
from trade_krono_cli.data_providers.factory import DataProviderFactory, _BenchResult


@pytest.fixture(autouse=True)
def _reset_factory_cache():
    """每个测试前后清理 benchmark 缓存。"""
    DataProviderFactory._rank_cache.clear()
    yield
    DataProviderFactory._rank_cache.clear()


def _make_mock_factory(success_names: list[str] | None = None) -> MagicMock:
    """创建一个 mock 的 factory，bench_all 返回指定成功状态的 provider 列表。"""
    factory = MagicMock()
    if success_names is None:
        success_names = ["baostock", "akshare", "mootdx"]
    factory.bench_all.return_value = [
        _BenchResult(name=name, latency_ms=50.0, success=True) for name in success_names
    ]
    return factory


class TestRankProviders:
    """rank_providers CLI 命令测试。"""

    def test_basic_ranking_outputs_table(self, capsys) -> None:
        """正常执行时输出排名表格。"""
        factory = _make_mock_factory()
        with patch(
            "trade_krono_cli.data_providers.factory.get_data_factory", return_value=factory,
        ), patch("trade_krono_cli.cli_commands.maintenance_sync._load_env"):
            rank_providers(ticker="sh.600519", workers=3, force=False)
        captured = capsys.readouterr()
        assert "Provider Benchmark" in captured.out
        assert "结果排名" in captured.out
        assert "baostock" in captured.out
        assert "akshare" in captured.out
        assert "已缓存 Provider 排序" in captured.out

    def test_force_flag_invalidates_cache(self, capsys) -> None:
        """force=True 时应调用 invalidate_rank_cache。"""
        factory = _make_mock_factory()
        with patch(
            "trade_krono_cli.data_providers.factory.get_data_factory", return_value=factory,
        ), patch("trade_krono_cli.cli_commands.maintenance_sync._load_env"):
            rank_providers(ticker="sh.600519", workers=3, force=True)
        factory.invalidate_rank_cache.assert_called_once_with("sh")

    def test_no_available_providers_exits(self) -> None:
        """没有可用 provider 时退出码为 1。"""
        factory = MagicMock()
        factory.bench_all.return_value = []
        with patch(
            "trade_krono_cli.data_providers.factory.get_data_factory", return_value=factory,
        ), patch("trade_krono_cli.cli_commands.maintenance_sync._load_env"):
            with pytest.raises(typer.Exit):
                rank_providers(ticker="sh.600519")

    def test_bj_ticker_type(self, capsys) -> None:
        """北交所 ticker 使用正确的 ticker_type。"""
        factory = _make_mock_factory()
        with patch(
            "trade_krono_cli.data_providers.factory.get_data_factory", return_value=factory,
        ), patch("trade_krono_cli.cli_commands.maintenance_sync._load_env"):
            rank_providers(ticker="bj.920001", workers=2, force=False)
        factory.bench_all.assert_called_once_with(ticker="bj.920001", workers=2)
        captured = capsys.readouterr()
        assert "已缓存 Provider 排序" in captured.out
