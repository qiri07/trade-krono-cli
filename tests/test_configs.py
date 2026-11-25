"""测试 configs 子配置模块。"""

from __future__ import annotations

from trade_krono_cli.configs.degradation import DegradationConfig
from trade_krono_cli.configs.filters import FilterConfig
from trade_krono_cli.configs.kronos import KronosConfig
from trade_krono_cli.configs.logging import LoggingConfig
from trade_krono_cli.configs.output import OutputConfig
from trade_krono_cli.configs.retry import RetryConfig
from trade_krono_cli.configs.risk import (
    DrawdownThresholds,
    MarketRegimeThresholds,
    RiskConfig,
    RiskWeights,
)
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
        assert config.min_confidence == 30.0
        assert config.allowed_signals == ("BUY", "OVERWEIGHT", "HOLD", "SELL")
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
        assert config.llm_provider == "agnes"
        assert config.deep_think_llm == "agnes-3.0-flash"
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


class TestRiskWeights:
    """测试风险权重配置。"""

    def test_default_weights(self) -> None:
        """默认权重之和约为 1.0。"""
        config = RiskWeights()
        errors = config.validate()
        assert len(errors) == 0
        # 只累加数值字段，避免未来新增非数值字段时 sum() 抛 TypeError
        total = sum(
            value for value in config.__dict__.values() if isinstance(value, (int, float))
        )
        assert 0.99 <= total <= 1.01

    def test_negative_weight_rejected(self) -> None:
        """负权重应被拒绝。"""
        config = RiskWeights(volatility=-0.1)
        errors = config.validate()
        assert any("volatility" in e for e in errors)

    def test_weight_sum_out_of_range(self) -> None:
        """权重之和超出 (0.99, 1.01) 应报错。"""
        config = RiskWeights(volatility=1.0, drawdown=1.0)
        errors = config.validate()
        assert any("权重之和" in e for e in errors)

    def test_merge(self) -> None:
        """merge() 覆盖单个字段。"""
        config = RiskWeights()
        merged = config.merge(volatility=0.5)
        assert merged.volatility == 0.5
        assert merged.drawdown == 0.2  # 其他字段不变


class TestDrawdownThresholds:
    """测试回撤阈值配置。"""

    def test_default_breakpoints(self) -> None:
        """默认断点应通过验证。"""
        config = DrawdownThresholds()
        errors = config.validate()
        assert len(errors) == 0

    def test_wrong_number_of_breakpoints(self) -> None:
        """断点数量不为 3 应报错。"""
        config = DrawdownThresholds(breakpoints=[(5.0, 20.0), (20.0, 60.0)])
        errors = config.validate()
        assert any("3 个点" in e for e in errors)

    def test_breakpoints_not_increasing(self) -> None:
        """左端点不递增应报错。"""
        config = DrawdownThresholds(
            breakpoints=[(20.0, 20.0), (10.0, 60.0), (40.0, 100.0)],
        )
        errors = config.validate()
        assert any("左端点须递增" in e for e in errors)

    def test_breakpoint_scores_not_increasing(self) -> None:
        """右端点分数不递增应报错。"""
        config = DrawdownThresholds(
            breakpoints=[(5.0, 20.0), (20.0, 60.0), (40.0, 20.0)],
        )
        errors = config.validate()
        assert any("右端点须递增" in e for e in errors)


class TestMarketRegimeThresholds:
    """测试市场状态阈值配置。"""

    def test_default_valid(self) -> None:
        """默认阈值应通过验证。"""
        config = MarketRegimeThresholds()
        errors = config.validate()
        assert len(errors) == 0

    def test_invalid_ordering(self) -> None:
        """bear_threshold >= neutral_low 应报错。"""
        config = MarketRegimeThresholds(bear_threshold=5.0, neutral_low=0.0)
        errors = config.validate()
        # 精确匹配校验器输出的阈值顺序错误，避免误匹配未来同前缀的其他错误
        assert any("阈值须满足" in e and "bear_threshold" in e for e in errors)


class TestRiskConfig:
    """测试完整风险配置。"""

    def test_default_valid(self) -> None:
        """默认配置应通过验证。"""
        config = RiskConfig()
        errors = config.validate()
        assert len(errors) == 0

    def test_var_confidence_out_of_range(self) -> None:
        """var_confidence 超出 [0, 1) 应报错。"""
        config = RiskConfig(var_confidence=1.0)
        errors = config.validate()
        assert any("var_confidence" in e for e in errors)

    def test_negative_fee(self) -> None:
        """负手续费应报错。"""
        config = RiskConfig(commission_bps=-1.0)
        errors = config.validate()
        assert any("commission_bps" in e for e in errors)

    def test_flat_merge(self) -> None:
        """平层合并覆盖。"""
        config = RiskConfig()
        merged = config.merge(beta_default=2.0)
        assert merged.beta_default == 2.0
        assert merged.var_confidence == 0.95  # 不变

    def test_nested_merge(self) -> None:
        """嵌套合并（weights__volatility）。"""
        config = RiskConfig()
        merged = config.merge(weights__volatility=0.5)
        assert merged.weights.volatility == 0.5
        assert merged.weights.drawdown == 0.2  # 不变


class TestLoggingConfig:
    """测试日志配置。"""

    def test_default_config(self) -> None:
        """默认配置。"""
        config = LoggingConfig()
        assert config.log_level == "INFO"
        assert config.log_json is False

    def test_custom_config(self) -> None:
        """自定义配置。"""
        config = LoggingConfig(log_level="DEBUG", log_json=True)
        assert config.log_level == "DEBUG"
        assert config.log_json is True

    def test_merge(self) -> None:
        """merge() 覆盖单个字段。"""
        config = LoggingConfig()
        merged = config.merge(log_level="WARNING")
        assert merged.log_level == "WARNING"
        assert merged.log_json is False  # 不变


class TestKronosConfigExtra:
    """Kronos 配置额外验证分支。"""

    def test_t_non_positive(self) -> None:
        """T <= 0 应报错。"""
        config = KronosConfig(T=0.0)
        errors = config.validate()
        assert any("T" in e and "必须 > 0" in e for e in errors)

    def test_top_p_out_of_range(self) -> None:
        """top_p 超出 (0, 1] 应报错。"""
        config = KronosConfig(top_p=0.0)
        errors = config.validate()
        assert any("top_p" in e for e in errors)
        config = KronosConfig(top_p=1.5)
        errors = config.validate()
        assert any("top_p" in e for e in errors)

    def test_model_name_empty(self) -> None:
        """model_name 不能为空。"""
        config = KronosConfig(model_name="")
        errors = config.validate()
        assert any("model_name" in e for e in errors)


class TestTAConfigExtra:
    """TA 配置额外验证分支。"""

    def test_output_language_empty(self) -> None:
        """output_language 不能为空。"""
        config = TAConfig(output_language="")
        errors = config.validate()
        assert any("output_language" in e for e in errors)


class TestDegradationConfigExtra:
    """降级配置额外验证分支。"""

    def test_invalid_degrade_mode(self) -> None:
        """非法 degrade_mode 应报错。"""
        config = DegradationConfig(degrade_mode="invalid_mode")
        errors = config.validate()
        assert any("DEGRADE_MODE" in e for e in errors)

    def test_cache_age_too_low(self) -> None:
        """ta_cache_max_age_days < 1 应报错。"""
        config = DegradationConfig(ta_cache_max_age_days=0)
        errors = config.validate()
        assert any("TA_CACHE_MAX_AGE_DAYS" in e for e in errors)

    def test_cache_age_too_high(self) -> None:
        """ta_cache_max_age_days > 365 应报错。"""
        config = DegradationConfig(ta_cache_max_age_days=400)
        errors = config.validate()
        assert any("TA_CACHE_MAX_AGE_DAYS" in e for e in errors)

    def test_fallback_enabled_wrong_mode(self) -> None:
        """启用缓存回退但模式不匹配应报错。"""
        config = DegradationConfig(
            degrade_mode="strict",
            ta_cache_fallback_enabled=True,
        )
        errors = config.validate()
        assert any("TA_CACHE_FALLBACK_ENABLED" in e for e in errors)
