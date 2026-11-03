"""tests for trade_krono_cli.llm_config — Key/端点/模型一致性解析。"""

from __future__ import annotations

import pytest

from trade_krono_cli.llm_config import (
    DEFAULT_AGNES_BASE_URL,
    DEFAULT_AGNES_MODEL,
    DEFAULT_NEMOTRON_BASE_URL,
    DEFAULT_NEMOTRON_MODEL,
    resolve_llm_config,
)

_ENV_VARS = (
    "LLM_PROVIDER",
    "NEMOTRON_BASE_URL",
    "BACKEND_URL",
    "LLM_BASE_URL",
    "NEMOTRON_MODEL",
    "AI_VERIFICATION_MODEL",
    "NVIDIA_API_KEY",
    "OPENROUTER_API_KEY",
    "DEEPSEEK_API_KEY",
    "OPENAI_API_KEY",
    "AGNES_API_KEY",
    "OPENAI_COMPATIBLE_API_KEY",
)


@pytest.fixture(autouse=True)
def _clean_llm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """每个用例前清空所有 LLM 相关环境变量，避免 shell 残留污染。"""
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)


class TestKeyEndpointMatching:
    """Key 必须与端点所属 Provider 匹配。"""

    def test_agnes_endpoint_prefers_agnes_key_over_stale_openai(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Agnes 端点不得使用残留的 OPENAI_API_KEY（历史 401 根因）。"""
        monkeypatch.setenv("BACKEND_URL", DEFAULT_AGNES_BASE_URL)
        monkeypatch.setenv("AGNES_API_KEY", "agnes-valid")
        monkeypatch.setenv("OPENAI_API_KEY", "stale-openai")
        cfg = resolve_llm_config()
        assert cfg.api_key == "agnes-valid"
        assert cfg.key_env == "AGNES_API_KEY"
        assert cfg.family == "agnes"

    def test_nvidia_endpoint_picks_nvidia_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NEMOTRON_BASE_URL", DEFAULT_NEMOTRON_BASE_URL)
        monkeypatch.setenv("NVIDIA_API_KEY", "nvidia-key")
        monkeypatch.setenv("OPENAI_API_KEY", "stale-openai")
        cfg = resolve_llm_config()
        assert cfg.api_key == "nvidia-key"
        assert cfg.key_env == "NVIDIA_API_KEY"

    def test_openrouter_endpoint_picks_openrouter_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NEMOTRON_BASE_URL", "https://openrouter.ai/api/v1")
        monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
        monkeypatch.setenv("NVIDIA_API_KEY", "nvidia-key")
        cfg = resolve_llm_config()
        assert cfg.api_key == "or-key"
        assert cfg.key_env == "OPENROUTER_API_KEY"

    def test_unknown_host_falls_back_to_provider_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BACKEND_URL", "https://example.com/v1")
        monkeypatch.setenv("LLM_PROVIDER", "deepseek")
        monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-key")
        monkeypatch.setenv("OPENAI_API_KEY", "stale-openai")
        cfg = resolve_llm_config()
        assert cfg.api_key == "ds-key"
        assert cfg.key_env == "DEEPSEEK_API_KEY"

    def test_no_keys_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BACKEND_URL", DEFAULT_AGNES_BASE_URL)
        cfg = resolve_llm_config()
        assert cfg.api_key is None
        assert cfg.key_env == "AGNES_API_KEY"


class TestBaseUrlResolution:
    """端点解析优先级与规范化。"""

    def test_nemotron_base_url_beats_backend_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NEMOTRON_BASE_URL", DEFAULT_NEMOTRON_BASE_URL)
        monkeypatch.setenv("BACKEND_URL", DEFAULT_AGNES_BASE_URL)
        assert resolve_llm_config().base_url == DEFAULT_NEMOTRON_BASE_URL

    def test_trailing_slash_is_stripped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BACKEND_URL", DEFAULT_AGNES_BASE_URL + "/")
        assert resolve_llm_config().base_url == DEFAULT_AGNES_BASE_URL

    def test_defaults_to_nemotron_when_nothing_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assert resolve_llm_config().base_url == DEFAULT_NEMOTRON_BASE_URL


class TestModelResolution:
    """模型名必须与端点族一致，避免 404。"""

    def test_agnes_endpoint_defaults_to_agnes_model(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BACKEND_URL", DEFAULT_AGNES_BASE_URL)
        assert resolve_llm_config().model == DEFAULT_AGNES_MODEL

    def test_nvidia_endpoint_defaults_to_nemotron_model(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NEMOTRON_BASE_URL", DEFAULT_NEMOTRON_BASE_URL)
        assert resolve_llm_config().model == DEFAULT_NEMOTRON_MODEL

    def test_agnes_model_is_replaced_on_nvidia_endpoint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AI_VERIFICATION_MODEL=agnes-* 但端点是 NVIDIA 时回落到 nemotron。"""
        monkeypatch.setenv("NEMOTRON_BASE_URL", DEFAULT_NEMOTRON_BASE_URL)
        monkeypatch.setenv("AI_VERIFICATION_MODEL", "agnes-3.0-flash")
        assert resolve_llm_config().model == DEFAULT_NEMOTRON_MODEL

    def test_explicit_matching_model_is_kept(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BACKEND_URL", DEFAULT_AGNES_BASE_URL)
        monkeypatch.setenv("AI_VERIFICATION_MODEL", "agnes-2.5-pro-alpha")
        assert resolve_llm_config().model == "agnes-2.5-pro-alpha"
