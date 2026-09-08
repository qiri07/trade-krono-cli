#!/usr/bin/env python3
"""trade_krono_cli.security 测试。"""

from __future__ import annotations

from trade_krono_cli.security import sanitize_for_log


class TestSanitizeForLog:
    """测试敏感信息脱敏功能。"""

    def test_sanitize_api_key(self) -> None:
        """API Key 被脱敏。"""
        text = "Using API key: sk-123456789abcdef"
        result = sanitize_for_log(text)
        assert "sk-123456789abcdef" not in result
        assert "sk-" in result or "****" in result

    def test_sanitize_token(self) -> None:
        """Token 被脱敏。"""
        text = "Token: abcdef123456"
        result = sanitize_for_log(text)
        assert "abcdef123456" not in result

    def test_sanitize_password(self) -> None:
        """Password 被脱敏。"""
        text = "Password: secret123"
        result = sanitize_for_log(text)
        assert "secret123" not in result

    def test_no_change_for_normal_text(self) -> None:
        """普通文本不被修改。"""
        text = "Normal log message"
        result = sanitize_for_log(text)
        assert result == text

    def test_sanitize_multiple_secrets(self) -> None:
        """多个敏感信息都被脱敏。"""
        text = "Key: sk-111 Token: tok-222"
        result = sanitize_for_log(text)
        assert "sk-111" not in result
        assert "tok-222" not in result

    def test_empty_string(self) -> None:
        """空字符串处理。"""
        assert sanitize_for_log("") == ""

    def test_none_handling(self) -> None:
        """None 输入处理。"""
        # 函数应该能处理非字符串输入
        result = sanitize_for_log(None)  # type: ignore
        assert result is None or result == "None"
