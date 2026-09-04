"""测试飞书推送核心模块（feishu_core.py）。"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from scripts.feishu_core import (
    build_buffett_card,
    build_ci_card,
    build_daily_card,
    build_text_card,
    get_webhook_url,
    load_config,
    send_notification,
)


class TestLoadConfig:
    """load_config 配置加载测试。"""

    def test_load_config_success(self, tmp_path) -> None:
        """成功加载配置文件。"""
        config_file = tmp_path / "config.json"
        config_file.write_text('{"webhook_url": "https://test.webhook"}', encoding="utf-8")

        config = load_config(config_file)
        assert config["webhook_url"] == "https://test.webhook"

    def test_load_config_missing_file(self, tmp_path) -> None:
        """配置文件不存在时应抛出 FileNotFoundError。"""
        with pytest.raises(FileNotFoundError):
            load_config(tmp_path / "nonexistent.json")

    def test_load_config_invalid_json(self, tmp_path) -> None:
        """配置文件 JSON 格式错误时应抛出 JSONDecodeError。"""
        config_file = tmp_path / "config.json"
        config_file.write_text("{invalid}", encoding="utf-8")

        with pytest.raises(Exception):  # JSONDecodeError
            load_config(config_file)

    def test_load_config_no_webhook(self, tmp_path) -> None:
        """配置文件既无 webhook_url 也无 channels 时应抛出 ValueError。"""
        config_file = tmp_path / "config.json"
        config_file.write_text("{}", encoding="utf-8")

        with pytest.raises(ValueError, match="webhook_url"):
            load_config(config_file)


class TestGetWebhookUrl:
    """get_webhook_url 测试。"""

    def test_default_channel(self) -> None:
        """默认频道应返回根 webhook_url。"""
        config = {"webhook_url": "https://default.webhook"}
        assert get_webhook_url(config, "default") == "https://default.webhook"

    def test_named_channel(self) -> None:
        """命名频道应返回对应 webhook_url。"""
        config = {
            "webhook_url": "https://default.webhook",
            "channels": {"alerts": {"webhook_url": "https://alerts.webhook"}},
        }
        assert get_webhook_url(config, "alerts") == "https://alerts.webhook"

    def test_missing_channel(self) -> None:
        """不存在的频道应返回根 webhook_url。"""
        config = {"webhook_url": "https://default.webhook"}
        assert get_webhook_url(config, "missing") == "https://default.webhook"

    def test_no_webhook_raises(self) -> None:
        """无 webhook_url 且无 channels 时应抛出 ValueError。"""
        config: dict = {}
        with pytest.raises(ValueError):
            get_webhook_url(config, "default")


class TestBuildCards:
    """卡片构建测试。"""

    def test_build_ci_card(self) -> None:
        """CI 卡片应包含正确结构。"""
        card = build_ci_card("success", "master", "abc123", "lint✅ test✅", "https://github.com/...")
        assert card["msg_type"] == "interactive"
        assert "card" in card
        assert "green" in card["card"]["header"]["template"]

    def test_build_daily_card(self) -> None:
        """Daily 卡片应包含正确结构。"""
        card = build_daily_card("success", "2026-09-04", "600519", "Top3", "https://github.com/...")
        assert card["msg_type"] == "interactive"
        assert "2026-09-04" in card["card"]["elements"][0]["text"]["content"]

    def test_build_buffett_card_missing_file(self) -> None:
        """结果文件不存在时应返回错误文本。"""
        card = build_buffett_card("outputs/results/nonexistent.txt")
        assert card["msg_type"] == "text"
        assert "结果文件不存在" in card["text"]

    def test_build_text_card(self) -> None:
        """文本卡片应包含正确结构。"""
        card = build_text_card("测试消息", "通知标题")
        assert card["msg_type"] == "interactive"
        assert "测试消息" in card["card"]["elements"][0]["text"]["content"]


class TestSendNotification:
    """send_notification 统一接口测试。"""

    def test_text_mode(self) -> None:
        """文本模式应调用 send_feishu。"""
        config = {"webhook_url": "https://test.webhook"}
        with patch("scripts.feishu_core.send_feishu") as mock_send:
            mock_send.return_value = True
            ok = send_notification("text", config, content="测试")
            assert ok is True
            mock_send.assert_called_once()

    def test_unknown_mode(self) -> None:
        """未知模式应返回 False。"""
        config = {"webhook_url": "https://test.webhook"}
        with patch("scripts.feishu_core.send_feishu") as mock_send:
            ok = send_notification("unknown", config, content="测试")
            assert ok is False
            mock_send.assert_not_called()

    def test_buffett_mode_with_missing_file(self) -> None:
        """ Buffett 模式处理缺失文件。"""
        config = {"webhook_url": "https://test.webhook"}
        with patch("scripts.feishu_core.send_feishu") as mock_send:
            mock_send.return_value = True
            ok = send_notification("buffett", config, result_file="nonexistent.txt")
            assert ok is True
