"""测试飞书通知模块（notify/feishu.py）— 新版 CLI 调用方式。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch


class TestSendFeishu:
    """send_feishu 基础功能测试。"""

    def test_no_config_and_no_env_returns_false(self) -> None:
        """未配置且无环境变量时应返回 False。"""
        from trade_krono_cli.notify.feishu import send_feishu

        with (
            patch("trade_krono_cli.notify.feishu._find_config") as mock_find,
            patch("trade_krono_cli.notify.feishu.subprocess.run") as mock_run,
        ):
            mock_find.return_value = None
            mock_run.return_value.returncode = 1
            result = send_feishu("test content")
            assert result is False

    def test_cli_success_returns_true(self) -> None:
        """CLI 成功执行时应返回 True。"""
        from trade_krono_cli.notify.feishu import send_feishu

        with (
            patch("trade_krono_cli.notify.feishu._find_config") as mock_find,
            patch("trade_krono_cli.notify.feishu.subprocess.run") as mock_run,
        ):
            mock_find.return_value = Path.home() / ".config" / "feishu-notify" / "config.json"
            mock_result = type("obj", (object,), {"returncode": 0, "stdout": "✅", "stderr": ""})()
            mock_run.return_value = mock_result

            result = send_feishu("这是一条测试消息")
            assert result is True
            mock_run.assert_called_once()

    def test_cli_failure_returns_false(self) -> None:
        """CLI 失败时应返回 False。"""
        from trade_krono_cli.notify.feishu import send_feishu

        with (
            patch("trade_krono_cli.notify.feishu._find_config") as mock_find,
            patch("trade_krono_cli.notify.feishu.subprocess.run") as mock_run,
        ):
            mock_find.return_value = None
            mock_result = type(
                "obj", (object,), {"returncode": 1, "stdout": "", "stderr": "error"}
            )()
            mock_run.return_value = mock_result

            result = send_feishu("msg")
            assert result is False

    def test_cli_timeout_returns_false(self) -> None:
        """CLI 超时应返回 False。"""
        import subprocess

        from trade_krono_cli.notify.feishu import send_feishu

        with (
            patch("trade_krono_cli.notify.feishu._find_config") as mock_find,
            patch("trade_krono_cli.notify.feishu.subprocess.run") as mock_run,
        ):
            mock_find.return_value = None
            mock_run.side_effect = subprocess.TimeoutExpired(cmd=["python"], timeout=30)

            result = send_feishu("msg")
            assert result is False

    def test_cli_file_not_found_returns_false(self) -> None:
        """CLI 脚本不存在时应返回 False。"""
        from trade_krono_cli.notify.feishu import send_feishu

        with (
            patch("trade_krono_cli.notify.feishu._find_config") as mock_find,
            patch("trade_krono_cli.notify.feishu.subprocess.run") as mock_run,
        ):
            mock_find.return_value = None
            mock_run.side_effect = FileNotFoundError("No such file")

            result = send_feishu("msg")
            assert result is False


class TestFindConfig:
    """_find_config 配置查找测试。"""

    def test_find_default_config(self, tmp_path) -> None:
        """默认配置路径存在时应返回该路径。"""
        import os

        from trade_krono_cli.notify.feishu import _find_config

        config_path = tmp_path / ".config" / "feishu-notify" / "config.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text("{}")

        with patch.dict(os.environ, {}, clear=True):
            with patch("trade_krono_cli.notify.feishu._DEFAULT_CONFIG_PATH", config_path):
                result = _find_config()
                assert result == config_path

    def test_find_env_config(self, tmp_path) -> None:
        """环境变量 FEISHU_CONFIG_PATH 应优先于默认路径。"""
        import os

        from trade_krono_cli.notify.feishu import _find_config

        env_config = tmp_path / "env_config.json"
        env_config.write_text("{}")

        with patch.dict(os.environ, {"FEISHU_CONFIG_PATH": str(env_config)}, clear=True):
            result = _find_config()
            assert result == env_config

    def test_find_none_when_no_config(self, tmp_path) -> None:
        """无配置文件时应返回 None。"""
        import os

        from trade_krono_cli.notify.feishu import _find_config

        with patch.dict(os.environ, {}, clear=True):
            with patch("trade_krono_cli.notify.feishu._DEFAULT_CONFIG_PATH", tmp_path / "missing"):
                result = _find_config()
                assert result is None


class TestSendNotificationModes:
    """不同模式发送测试。"""

    def test_text_mode(self) -> None:
        """text 模式应包含 --content 参数。"""
        from trade_krono_cli.notify.feishu import send_feishu

        with (
            patch("trade_krono_cli.notify.feishu._find_config") as mock_find,
            patch("trade_krono_cli.notify.feishu.subprocess.run") as mock_run,
        ):
            mock_find.return_value = None
            mock_result = type("obj", (object,), {"returncode": 0, "stdout": "", "stderr": ""})()
            mock_run.return_value = mock_result

            send_feishu("hello world", mode="text")
            args = mock_run.call_args[0][0]
            assert "text" in args
            assert "--content" in args
            assert "hello world" in args

    def test_buffett_mode(self) -> None:
        """buffett 模式应包含 --result-file 参数。"""
        from trade_krono_cli.notify.feishu import send_feishu

        with (
            patch("trade_krono_cli.notify.feishu._find_config") as mock_find,
            patch("trade_krono_cli.notify.feishu.subprocess.run") as mock_run,
        ):
            mock_find.return_value = None
            mock_result = type("obj", (object,), {"returncode": 0, "stdout": "", "stderr": ""})()
            mock_run.return_value = mock_result

            send_feishu("", mode="buffett", result_file="outputs/results/buffett.txt")
            args = mock_run.call_args[0][0]
            assert "buffett" in args
            assert "--result-file" in args
            assert "outputs/results/buffett.txt" in args

    def test_ci_mode(self) -> None:
        """ci 模式应包含必要参数。"""
        from trade_krono_cli.notify.feishu import send_feishu

        with (
            patch("trade_krono_cli.notify.feishu._find_config") as mock_find,
            patch("trade_krono_cli.notify.feishu.subprocess.run") as mock_run,
        ):
            mock_find.return_value = None
            mock_result = type("obj", (object,), {"returncode": 0, "stdout": "", "stderr": ""})()
            mock_run.return_value = mock_result

            send_feishu(
                "",
                mode="ci",
                status="success",
                branch="master",
                commit="abc123",
                jobs="lint✅ test✅",
                run_url="https://github.com/...",
            )
            args = mock_run.call_args[0][0]
            assert "ci" in args
            assert "--status" in args
            assert "success" in args

    def test_daily_mode(self) -> None:
        """daily 模式应包含必要参数。"""
        from trade_krono_cli.notify.feishu import send_feishu

        with (
            patch("trade_krono_cli.notify.feishu._find_config") as mock_find,
            patch("trade_krono_cli.notify.feishu.subprocess.run") as mock_run,
        ):
            mock_find.return_value = None
            mock_result = type("obj", (object,), {"returncode": 0, "stdout": "", "stderr": ""})()
            mock_run.return_value = mock_result

            send_feishu(
                "",
                mode="daily",
                status="success",
                date="2026-09-04",
                tickers="600519,000858",
                top3="600519:BUY",
                run_url="https://github.com/...",
            )
            args = mock_run.call_args[0][0]
            assert "daily" in args
            assert "--date" in args
            assert "2026-09-04" in args

    def test_channel_parameter(self) -> None:
        """channel 参数应传递到 CLI。"""
        from trade_krono_cli.notify.feishu import send_feishu

        with (
            patch("trade_krono_cli.notify.feishu._find_config") as mock_find,
            patch("trade_krono_cli.notify.feishu.subprocess.run") as mock_run,
        ):
            mock_find.return_value = None
            mock_result = type("obj", (object,), {"returncode": 0, "stdout": "", "stderr": ""})()
            mock_run.return_value = mock_result

            send_feishu("msg", channel="alerts")
            args = mock_run.call_args[0][0]
            assert "--channel" in args
            assert "alerts" in args

    def test_missing_result_file_for_buffett(self) -> None:
        """buffett 模式缺少 result_file 应返回 False。"""
        from trade_krono_cli.notify.feishu import send_feishu

        with patch("trade_krono_cli.notify.feishu._find_config") as mock_find:
            mock_find.return_value = None
            result = send_feishu("", mode="buffett")
            assert result is False
