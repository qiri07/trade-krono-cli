"""测试 CLI 参数解析。"""
import pytest
from unittest.mock import patch, MagicMock
from typer.testing import CliRunner

from trade_krono_cli.cli import app


@pytest.fixture
def runner():
    return CliRunner()


def test_cli_help(runner):
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "trade-krono-cli" in result.output


def test_run_command_help(runner):
    result = runner.invoke(app, ["run", "--help"])
    assert result.exit_code == 0
    assert "--tickers" in result.output
    assert "--date" in result.output


def test_ta_command_help(runner):
    result = runner.invoke(app, ["ta", "--help"])
    assert result.exit_code == 0


def test_kronos_command_help(runner):
    result = runner.invoke(app, ["kronos", "--help"])
    assert result.exit_code == 0


def test_status_command(runner):
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0


def test_clear_cache_command(runner):
    result = runner.invoke(app, ["clear-cache"])
    assert result.exit_code == 0


def test_run_missing_tickers(runner):
    """不提供股票列表时应报错。"""
    with patch("trade_krono_cli.cli._load_env"):
        result = runner.invoke(app, ["run", "--date", "2026-08-11"])
        assert result.exit_code != 0
        assert "股票列表为空" in result.output


def test_load_tickers_from_string():
    from trade_krono_cli.cli import _load_tickers
    tickers = _load_tickers("600519,000858,600036", None)
    assert tickers == ["600519", "000858", "600036"]


def test_load_tickers_from_config():
    from trade_krono_cli.cli import _load_tickers
    import tempfile
    import os
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("600519\n")
        f.write("# 注释行\n")
        f.write("000858\n")
        path = f.name
    try:
        tickers = _load_tickers(None, path)
        assert tickers == ["600519", "000858"]
    finally:
        os.unlink(path)


def test_load_tickers_empty(runner):
    with patch("trade_krono_cli.cli._load_env"):
        result = runner.invoke(app, ["ta", "--tickers", ""])
        assert result.exit_code != 0
