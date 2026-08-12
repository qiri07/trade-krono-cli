"""测试 CLI 参数解析。"""
import pytest
from pathlib import Path
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


def test_load_tickers_from_config_not_found():
    """配置文件不存在时应抛出 Exit。"""
    from trade_krono_cli.cli import _load_tickers
    from typer import Exit
    with pytest.raises(Exit):
        _load_tickers(None, "/nonexistent/path.txt")


def test_sanitize_path_valid(tmp_path):
    """合法路径应在项目根目录内。"""
    from trade_krono_cli.cli import _sanitize_path
    project_root = tmp_path
    p = tmp_path / "outputs" / "result.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    result = _sanitize_path(str(p), "Test", project_root)
    assert result == p.resolve()


def test_sanitize_path_traversal_rejected():
    """路径遍历应被拒绝。"""
    from trade_krono_cli.cli import _sanitize_path
    from typer import Exit
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        with pytest.raises(Exit):
            _sanitize_path("/etc/passwd", "Test", Path(td))


def test_sanitize_path_symlink_escape_rejected(tmp_path):
    """通过符号链接绕过项目根目录的检查应被拒绝。"""
    from trade_krono_cli.cli import _sanitize_path
    from typer import Exit

    # 在项目根目录下创建指向 /etc 的符号链接
    link = tmp_path / "link"
    link.symlink_to("/etc")

    with pytest.raises(Exit):
        _sanitize_path(str(link / "passwd"), "Test", tmp_path)


def test_sanitize_path_symlink_to_valid_dir_accepted(tmp_path):
    """指向项目内合法目录的符号链接应被接受。"""
    from trade_krono_cli.cli import _sanitize_path

    out_dir = tmp_path / "outputs"
    out_dir.mkdir()
    link = tmp_path / "link_out"
    link.symlink_to(out_dir)

    result = _sanitize_path(str(link / "result.json"), "Test", tmp_path)
    assert result == (out_dir / "result.json").resolve()


def test_repo_commands_help(runner):
    """repo 子命令应显示帮助信息。"""
    result = runner.invoke(app, ["repo", "--help"])
    assert result.exit_code == 0
    assert "status" in result.output
    assert "doctor" in result.output
    assert "update" in result.output
    assert "pin" in result.output


def test_repo_status_command(runner):
    """repo-status 应正常运行（不崩溃）。"""
    result = runner.invoke(app, ["repo-status"])
    # 可能因缺少外部 repo 配置而退出非 0，但不应是 help 错误
    assert "repo-status" in result.output or result.exit_code == 0


def test_eval_prediction_command_help(runner):
    """eval 命令应显示帮助。"""
    result = runner.invoke(app, ["eval-prediction", "--help"])
    assert result.exit_code == 0


def test_history_command_help(runner):
    """history 命令应显示帮助。"""
    result = runner.invoke(app, ["history", "--help"])
    assert result.exit_code == 0
    assert "--ticker" in result.output


def test_run_command_with_tickers_patched(runner):
    """run 命令传入有效 tickers 应进入 pipeline 逻辑。"""
    from unittest.mock import patch, MagicMock
    mock_pipeline = MagicMock()
    mock_pipeline.run_parallel.return_value = []
    with patch("trade_krono_cli.cli._load_env"), \
         patch("trade_krono_cli.pipeline.QuantPipeline", return_value=mock_pipeline):
        result = runner.invoke(app, [
            "run", "--tickers", "600519",
            "--date", "2026-08-11",
        ])
        assert result.exit_code == 0


def test_ta_command_with_tickers_patched(runner):
    """ta 命令传入有效 tickers 应进入 pipeline 逻辑。"""
    from unittest.mock import patch, MagicMock
    mock_pipeline = MagicMock()
    mock_pipeline.run_ta_only.return_value = []
    with patch("trade_krono_cli.cli._load_env"), \
         patch("trade_krono_cli.pipeline.QuantPipeline", return_value=mock_pipeline):
        result = runner.invoke(app, [
            "ta", "--tickers", "600519",
            "--date", "2026-08-11",
        ])
        assert result.exit_code == 0


def test_kronos_command_with_tickers_patched(runner):
    """kronos 命令传入有效 tickers 应进入 pipeline 逻辑。"""
    from unittest.mock import patch, MagicMock
    mock_pipeline = MagicMock()
    mock_pipeline.run_kronos_only.return_value = []
    with patch("trade_krono_cli.cli._load_env"), \
         patch("trade_krono_cli.pipeline.QuantPipeline", return_value=mock_pipeline):
        result = runner.invoke(app, [
            "kronos", "--tickers", "600519",
            "--date", "2026-08-11",
        ])
        assert result.exit_code == 0


def test_eval_command_with_tickers_patched(runner):
    """eval 命令应调用 run_evaluation。"""
    from unittest.mock import patch
    with patch("trade_krono_cli.cli._load_env"), \
         patch("trade_krono_cli.prediction_eval.run_evaluation") as mock_eval:
        result = runner.invoke(app, [
            "eval-prediction", "--from", "2026-01-01", "--to", "2026-08-11",
            "--tickers", "600519,000858",
        ])
        assert result.exit_code == 0
        mock_eval.assert_called_once()
        call_kwargs = mock_eval.call_args[1]
        assert call_kwargs["from_date"] == "2026-01-01"
        assert call_kwargs["to_date"] == "2026-08-11"
        assert call_kwargs["tickers"] == ["600519", "000858"]


def test_eval_command_latest_flag(runner):
    """eval --latest 应传递 latest=True。"""
    from unittest.mock import patch
    with patch("trade_krono_cli.cli._load_env"), \
         patch("trade_krono_cli.prediction_eval.run_evaluation") as mock_eval:
        result = runner.invoke(app, ["eval-prediction", "--latest"])
        assert result.exit_code == 0
        mock_eval.assert_called_once()
        assert mock_eval.call_args[1]["latest"] is True
