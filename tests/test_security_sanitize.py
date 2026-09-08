#!/usr/bin/env python3
"""trade_krono_cli.security 测试。"""

from __future__ import annotations

import pytest

from trade_krono_cli.security import sanitize_for_log


class TestSanitizeForLog:
    """测试敏感信息脱敏功能（仅覆盖 API Key / Bearer Token）。"""

    def test_sanitize_openai_key(self) -> None:
        """OpenAI 风格 API Key 被脱敏。"""
        text = "Using API key: sk-1234567890abcdef1234"
        result = sanitize_for_log(text)
        assert "sk-1234567890abcdef1234" not in result
        assert "[REDACTED_KEY]" in result

    def test_sanitize_anthropic_key(self) -> None:
        """Anthropic 风格 API Key 被脱敏。"""
        text = "Key: sk-ant-ABCDefghij1234567890xyz"
        result = sanitize_for_log(text)
        assert "sk-ant-ABCDefghij1234567890xyz" not in result
        assert "[REDACTED_KEY]" in result

    def test_sanitize_bearer_token(self) -> None:
        """Bearer Token 被脱敏。"""
        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.test"
        result = sanitize_for_log(text)
        assert "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.test" not in result
        assert "[REDACTED_KEY]" in result

    def test_no_change_for_normal_text(self) -> None:
        """普通文本不被修改。"""
        text = "Normal log message"
        result = sanitize_for_log(text)
        assert result == text

    def test_sanitize_multiple_secrets(self) -> None:
        """多个敏感信息都被脱敏。"""
        text = "Key: sk-aabbccdd1234567890xyz Token: Bearer abc123.def456"
        result = sanitize_for_log(text)
        assert "sk-aabbccdd1234567890xyz" not in result
        assert "Bearer abc123.def456" not in result
        assert result.count("[REDACTED_KEY]") == 2

    def test_empty_string(self) -> None:
        """空字符串处理。"""
        assert sanitize_for_log("") == ""

    def test_none_handling(self) -> None:
        """None 输入抛出 TypeError（函数期望 str）。"""
        with pytest.raises(TypeError):
            sanitize_for_log(None)  # type: ignore[arg-type]
