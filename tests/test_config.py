"""测试 trade_krono_cli.config — Settings 数据类。"""

from __future__ import annotations

from pathlib import Path

import pytest

from trade_krono_cli.config import Settings, clear_settings, get_settings


class TestSettingsDefaults:
    """Settings 默认值测试（使用 monkeypatch 清除环境变量干扰）。"""

    def test_llm_provider_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        monkeypatch.delenv("DEEP_THINK_LLM", raising=False)
        monkeypatch.delenv("QUICK_THINK_LLM", raising=False)
        monkeypatch.delenv("KRONOS_MODEL", raising=False)
        clear_settings()
        s = Settings()
        assert s.llm_provider == "deepseek"
        assert s.deep_think_llm == "deepseek-chat"
        assert s.quick_think_llm == "deepseek-chat"
        assert s.kronos_model == "kronos-base"

    def test_max_debate_rounds_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MAX_DEBATE_ROUNDS", raising=False)
        clear_settings()
        s = Settings()
        assert s.max_debate_rounds == 1

    def test_max_risk_discuss_rounds_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MAX_RISK_DISCUSS_ROUNDS", raising=False)
        clear_settings()
        s = Settings()
        assert s.max_risk_discuss_rounds == 1

    def test_checkpoint_enabled_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CHECKPOINT_ENABLED", raising=False)
        clear_settings()
        s = Settings()
        assert s.checkpoint_enabled is True

    def test_output_language_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OUTPUT_LANGUAGE", raising=False)
        clear_settings()
        s = Settings()
        assert s.output_language == "Chinese"

    def test_kronos_lookback_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("KRONOS_LOOKBACK", raising=False)
        clear_settings()
        s = Settings()
        assert s.kronos_lookback == 400

    def test_kronos_pred_len_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("KRONOS_PRED_LEN", raising=False)
        clear_settings()
        s = Settings()
        assert s.kronos_pred_len == 30

    def test_kronos_sample_count_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("KRONOS_SAMPLE_COUNT", raising=False)
        clear_settings()
        s = Settings()
        assert s.kronos_sample_count == 5

    def test_kronos_T_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("KRONOS_T", raising=False)
        clear_settings()
        s = Settings()
        assert s.kronos_T == 1.0

    def test_kronos_top_p_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("KRONOS_TOP_P", raising=False)
        clear_settings()
        s = Settings()
        assert s.kronos_top_p == 0.9

    def test_kronos_use_sample_confidence_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("KRONOS_USE_SAMPLE_CONFIDENCE", raising=False)
        clear_settings()
        s = Settings()
        assert s.kronos_use_sample_confidence is False

    def test_tradingagents_root_default(self) -> None:
        s = Settings()
        assert s.tradingagents_root.name == "TradingAgents-astock"

    def test_kronos_root_default(self) -> None:
        s = Settings()
        assert s.kronos_root.name == "Kronos"


class TestSettingsEnvironmentOverrides:
    """Settings 环境变量覆盖测试。"""

    def test_cache_dir_from_env(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("TRADING_KRONO_CACHE_DIR", str(tmp_path / "cache"))
        s = Settings()
        assert s.cache_dir == tmp_path / "cache"

    def test_results_dir_from_env(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("TRADING_KRONO_RESULTS_DIR", str(tmp_path / "results"))
        s = Settings()
        assert s.results_dir == tmp_path / "results"

    def test_llm_provider_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        s = Settings()
        assert s.llm_provider == "openai"

    def test_kronos_device_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KRONOS_DEVICE", "cuda:0")
        s = Settings()
        assert s.kronos_device == "cuda:0"

    def test_kronos_lookback_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KRONOS_LOOKBACK", "200")
        s = Settings()
        assert s.kronos_lookback == 200

    def test_kronos_pred_len_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KRONOS_PRED_LEN", "60")
        s = Settings()
        assert s.kronos_pred_len == 60

    def test_max_debate_rounds_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAX_DEBATE_ROUNDS", "3")
        s = Settings()
        assert s.max_debate_rounds == 3

    def test_checkpoint_disabled_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CHECKPOINT_ENABLED", "false")
        s = Settings()
        assert s.checkpoint_enabled is False

    def test_output_language_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OUTPUT_LANGUAGE", "English")
        s = Settings()
        assert s.output_language == "English"


class TestGetSettings:
    """get_settings 单例测试。"""

    def test_returns_settings_instance(self) -> None:
        s = get_settings()
        assert isinstance(s, Settings)

    def test_singleton_same_instance(self) -> None:
        s1 = get_settings()
        s2 = get_settings()
        assert s1 is s2


class TestSettingsImmutable:
    """Settings 为 frozen dataclass，不可变。"""

    def test_cannot_modify_field(self) -> None:
        s = Settings()
        with pytest.raises(Exception):  # FrozenInstanceError
            s.llm_provider = "custom"
