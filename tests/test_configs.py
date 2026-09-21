"""测试 configs 子配置模块。"""

from __future__ import annotations

from trade_krono_cli.configs.degradation import DegradationConfig
from trade_krono_cli.configs.filters import FilterConfig
from trade_krono_cli.configs.kronos import KronosConfig
from trade_krono_cli.configs.output import OutputConfig
from trade_krono_cli.configs.retry import RetryConfig
from trade_krono_cli.configs.scoring import ScoringConfig
from trade_krono_cli.configs.ta import TAConfig


class TestScoringConfig:
    """测试打分配置。"""

    def test_default_linear_strategy(self) -> None:
        """默认线性策略配置。"""
        config = ScoringConfig()
        assert config.ta_confidence_weight == 0.40
        assert config.change_pct_weight == 0.30
        assert config.direction_base_weight == 0.10
        assert config.uncertainty_base_weight == 0.10
        assert config.risk_penalty_weight == 0.15

    def test_uncertainty_thresholds(self) -> None:
        """测试不确定性阈值默认值。"""
        config = ScoringConfig()
        assert config.uncertainty_high_threshold == 70.0
        assert config.uncertainty_med_threshold == 50.0
        assert config.uncertainty_high_bonus == 3.0
        assert config.uncertainty_med_bonus == 1.0
        assert config.uncertainty_low_penalty == -2.0

    def test_merge_overrides(self) -> None:
        """测试合并覆盖。"""
        config = ScoringConfig()
        merged = config.merge(ta_confidence_weight=0.5)
        assert merged.ta_confidence_weight == 0.5
        assert merged.change_pct_weight == 0.30  # 其他字段不变

    def test_validate_weight_sum(self) -> None:
        """权重之和应在 (0, 1.0] 范围内。"""
        config = ScoringConfig(
            ta_confidence_weight=0.3,
            change_pct_weight=0.3,
            direction_base_weight=0.2,
            uncertainty_base_weight=0.1,
            risk_penalty_weight=0.1,
        )
        errors = config.validate()
        assert len(errors) == 0

    def test_validate_invalid_threshold_order(self) -> None:
        """med_threshold >= high_threshold 应报错。"""
        config = ScoringConfig(
            uncertainty_high_threshold=50.0,
            uncertainty_med_threshold=70.0,
        )
        errors = config.validate()
        assert any("uncertainty_med_threshold" in e for e in errors)


class TestFilterConfig:
    """测试过滤配置。"""

    def test_default_config(self) -> None:
        """默认过滤配置。"""
        config = FilterConfig()
        assert config.min_confidence == 55.0
        assert config.allowed_signals == ("BUY", "OVERWEIGHT", "HOLD")
        assert config.exclude_st is True
        assert config.exclude_low_price is True
        assert config.low_price_threshold == 3.0

    def test_custom_config(self) -> None:
        """自定义配置。"""
        config = FilterConfig(
            min_confidence=60.0,
            allowed_signals=("BUY", "HOLD"),
            exclude_st=False,
        )
        assert config.min_confidence == 60.0
        assert config.allowed_signals == ("BUY", "HOLD")
        assert config.exclude_st is False

    def test_pe_range_parsing(self) -> None:
        """PE 范围设置。"""
        config = FilterConfig(pe_range=(10.0, 25.0))
        assert config.pe_range == (10.0, 25.0)

    def test_pb_range_parsing(self) -> None:
        """PB 范围设置。"""
        config = FilterConfig(pb_range=(0.5, 2.0))
        assert config.pb_range == (0.5, 2.0)

    def test_validate_min_confidence(self) -> None:
        """min_confidence 应在 [0, 100] 范围内。"""
        config = FilterConfig(min_confidence=150.0)
        errors = config.validate()
        assert len(errors) > 0

    def test_merge(self) -> None:
        """测试合并覆盖。"""
        config = FilterConfig(min_confidence=55.0)
        merged = config.merge(min_confidence=60.0)
        assert merged.min_confidence == 60.0
        assert merged.exclude_st is True  # 其他字段不变


class TestKronosConfig:
    """测试 Kronos 预测配置。"""

    def test_default_config(self) -> None:
        """默认配置。"""
        config = KronosConfig()
        assert config.model_name == "kronos-base"
        assert config.lookback == 400
        assert config.pred_len == 30
        assert config.sample_count == 5
        assert config.T == 1.0
        assert config.top_p == 0.9
        assert config.device == "cpu"

    def test_custom_config(self) -> None:
        """自定义配置。"""
        config = KronosConfig(
            model_name="kronos-small",
            lookback=200,
            pred_len=15,
            sample_count=10,
        )
        assert config.model_name == "kronos-small"
        assert config.lookback == 200
        assert config.pred_len == 15
        assert config.sample_count == 10

    def test_validate_pred_len(self) -> None:
        """pred_len 必须 >= 1。"""
        config = KronosConfig(pred_len=0)
        errors = config.validate()
        assert any("pred_len" in e for e in errors)

    def test_validate_lookback(self) -> None:
        """lookback 必须 >= 10。"""
        config = KronosConfig(lookback=5)
        errors = config.validate()
        assert any("lookback" in e for e in errors)

    def test_merge(self) -> None:
        """测试合并覆盖。"""
        config = KronosConfig()
        merged = config.merge(sample_count=10)
        assert merged.sample_count == 10
        assert merged.pred_len == 30  # 其他字段不变


class TestTAConfig:
    """测试 TA 分析配置。"""

    def test_default_config(self) -> None:
        """默认配置。"""
        config = TAConfig()
        assert config.llm_provider == "deepseek"
        assert config.deep_think_llm == "deepseek-chat"
        assert config.max_debate_rounds == 1
        assert config.output_language == "Chinese"

    def test_custom_config(self) -> None:
        """自定义配置。"""
        config = TAConfig(
            llm_provider="openai",
            deep_think_llm="gpt-4",
            max_debate_rounds=3,
        )
        assert config.llm_provider == "openai"
        assert config.deep_think_llm == "gpt-4"
        assert config.max_debate_rounds == 3

    def test_validate_provider_empty(self) -> None:
        """llm_provider 不能为空。"""
        config = TAConfig(llm_provider="")
        errors = config.validate()
        assert any("llm_provider" in e for e in errors)

    def test_validate_debate_rounds(self) -> None:
        """max_debate_rounds 必须 >= 1。"""
        config = TAConfig(max_debate_rounds=0)
        errors = config.validate()
        assert any("max_debate_rounds" in e for e in errors)


class TestRetryConfig:
    """测试重试配置。"""

    def test_default_config(self) -> None:
        """默认配置。"""
        config = RetryConfig()
        assert config.retry_max_attempts == 3
        assert config.retry_base_delay == 2.0
        assert config.retry_jitter is True

    def test_custom_config(self) -> None:
        """自定义配置。"""
        config = RetryConfig(
            retry_max_attempts=5,
            retry_base_delay=5.0,
            retry_jitter=False,
        )
        assert config.retry_max_attempts == 5
        assert config.retry_base_delay == 5.0
        assert config.retry_jitter is False

    def test_merge(self) -> None:
        """merge() 方法合并参数。"""
        base = RetryConfig()
        merged = base.merge(retry_max_attempts=5, retry_jitter=False)
        assert merged.retry_max_attempts == 5
        assert merged.retry_jitter is False
        assert merged.retry_base_delay == 2.0
        assert merged.retry_rate_limit_backoff is True

    def test_validate_default_ok(self) -> None:
        """默认配置应无验证错误。"""
        assert RetryConfig().validate() == []

    def test_validate_max_attempts_too_low(self) -> None:
        """retry_max_attempts < 1 应报错。"""
        errors = RetryConfig(retry_max_attempts=0).validate()
        assert len(errors) == 1
        assert "RETRY_MAX_ATTEMPTS 必须 >= 1" in errors[0]

    def test_validate_max_attempts_too_high(self) -> None:
        """retry_max_attempts > 10 应报错。"""
        errors = RetryConfig(retry_max_attempts=11).validate()
        assert len(errors) == 1
        assert "RETRY_MAX_ATTEMPTS 不应超过 10" in errors[0]

    def test_validate_base_delay_zero(self) -> None:
        """retry_base_delay <= 0 应报错。"""
        errors = RetryConfig(retry_base_delay=0).validate()
        assert len(errors) == 1
        assert "RETRY_BASE_DELAY 必须 > 0" in errors[0]

    def test_validate_base_delay_too_high(self) -> None:
        """retry_base_delay > 60 应报错。"""
        errors = RetryConfig(retry_base_delay=61).validate()
        assert len(errors) == 1
        assert "RETRY_BASE_DELAY 不应超过 60s" in errors[0]

    def test_validate_rate_limit_max_wait_zero(self) -> None:
        """retry_rate_limit_max_wait <= 0 应报错。"""
        errors = RetryConfig(retry_rate_limit_max_wait=0).validate()
        assert len(errors) == 1
        assert "RETRY_RATE_LIMIT_MAX_WAIT 必须 > 0" in errors[0]

    def test_validate_rate_limit_max_wait_too_high(self) -> None:
        """retry_rate_limit_max_wait > 300 应报错。"""
        errors = RetryConfig(retry_rate_limit_max_wait=301).validate()
        assert len(errors) == 1
        assert "RETRY_RATE_LIMIT_MAX_WAIT 不应超过 300s" in errors[0]


class TestDegradationConfig:
    """测试降级配置。"""

    def test_default_config(self) -> None:
        """默认配置。"""
        config = DegradationConfig()
        assert config.degrade_mode == "strict"
        assert config.ta_cache_fallback_enabled is False

    def test_ta_only_mode(self) -> None:
        """TA-only 降级模式。"""
        config = DegradationConfig(degrade_mode="ta_only_on_kronos_fail")
        assert config.degrade_mode == "ta_only_on_kronos_fail"

    def test_cache_fallback_mode(self) -> None:
        """缓存回退模式。"""
        config = DegradationConfig(
            degrade_mode="ta_cache_fallback",
            ta_cache_fallback_enabled=True,
            ta_cache_max_age_days=7,
        )
        assert config.degrade_mode == "ta_cache_fallback"
        assert config.ta_cache_fallback_enabled is True
        assert config.ta_cache_max_age_days == 7


class TestOutputConfig:
    """测试输出配置。"""

    def test_default_config(self) -> None:
        """默认配置。"""
        config = OutputConfig()
        assert config.output_dir.name == "outputs"

    def test_custom_output_dir(self) -> None:
        """自定义输出目录。"""
        config = OutputConfig(output_dir="/tmp/test_outputs")
        assert str(config.output_dir) == "/tmp/test_outputs"
