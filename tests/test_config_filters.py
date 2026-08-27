"""测试 FilterConfig 配置类。"""
from __future__ import annotations

import pytest

from trade_krono_cli.configs.filters import FilterConfig
from trade_krono_cli.stock_filter import MinValueRule, RangeRule


class TestFilterConfig:
    def test_default_values(self):
        cfg = FilterConfig()
        assert cfg.min_confidence == 55.0
        assert cfg.allowed_signals == ("BUY", "HOLD")
        assert cfg.exclude_st is True
        assert cfg.exclude_low_price is True
        assert cfg.low_price_threshold == 3.0
        assert cfg.min_pb is None
        assert cfg.market_cap_range is None
        assert cfg.pe_range is None
        assert cfg.pb_range is None
        assert cfg.universe_source == "akshare"
        assert cfg.filter_rules == []

    def test_custom_values(self):
        cfg = FilterConfig(
            min_confidence=70.0,
            allowed_signals=("BUY",),
            exclude_st=False,
            low_price_threshold=5.0,
            min_pb=0.5,
            market_cap_range=(100.0, 5000.0),
            pe_range=(5.0, 30.0),
            pb_range=(0.5, 5.0),
            universe_source="mootdx",
        )
        assert cfg.min_confidence == 70.0
        assert cfg.allowed_signals == ("BUY",)
        assert cfg.exclude_st is False
        assert cfg.exclude_low_price is True  # 默认未改
        assert cfg.low_price_threshold == 5.0
        assert cfg.min_pb == 0.5
        assert cfg.market_cap_range == (100.0, 5000.0)
        assert cfg.universe_source == "mootdx"

    def test_merge_overrides_min_confidence(self):
        cfg = FilterConfig(min_confidence=55.0)
        merged = cfg.merge(min_confidence=65.0)
        assert merged.min_confidence == 65.0
        assert merged.allowed_signals == ("BUY", "HOLD")  # 未覆盖保持默认

    def test_merge_overrides_all(self):
        cfg = FilterConfig(min_confidence=55.0)
        merged = cfg.merge(
            min_confidence=80.0,
            exclude_st=False,
            low_price_threshold=10.0,
            min_pb=1.0,
        )
        assert merged.min_confidence == 80.0
        assert merged.exclude_st is False
        assert merged.low_price_threshold == 10.0
        assert merged.min_pb == 1.0

    def test_merge_empty_no_change(self):
        cfg = FilterConfig(min_confidence=70.0)
        merged = cfg.merge()
        assert merged.min_confidence == 70.0

    def test_merge_with_filter_rules(self):
        rules = [MinValueRule("confidence", 60.0), RangeRule("market_cap", 50.0, 5000.0)]
        cfg = FilterConfig(filter_rules=rules)
        assert len(cfg.filter_rules) == 2
        assert cfg.filter_rules[0].field == "confidence"

    def test_merge_updates_filter_rules(self):
        cfg = FilterConfig(filter_rules=[])
        new_rules = [MinValueRule("risk_score", 0.5)]
        merged = cfg.merge(filter_rules=new_rules)
        assert len(merged.filter_rules) == 1
        assert merged.filter_rules[0].field == "risk_score"
        # 其他字段不变
        assert merged.exclude_st is True

    def test_validate_valid_defaults(self):
        cfg = FilterConfig()
        errors = cfg.validate()
        assert errors == []

    def test_validate_min_confidence_too_low(self):
        cfg = FilterConfig(min_confidence=-5.0)
        errors = cfg.validate()
        assert len(errors) == 1
        assert "min_confidence" in errors[0]

    def test_validate_min_confidence_too_high(self):
        cfg = FilterConfig(min_confidence=105.0)
        errors = cfg.validate()
        assert len(errors) == 1
        assert "min_confidence" in errors[0]

    def test_validate_min_confidence_boundary_zero(self):
        cfg = FilterConfig(min_confidence=0.0)
        errors = cfg.validate()
        assert errors == []

    def test_validate_min_confidence_boundary_hundred(self):
        cfg = FilterConfig(min_confidence=100.0)
        errors = cfg.validate()
        assert errors == []

    def test_validate_empty_signals(self):
        cfg = FilterConfig(allowed_signals=())
        errors = cfg.validate()
        assert len(errors) == 1
        assert "allowed_signals" in errors[0]

    def test_validate_low_price_threshold_zero(self):
        cfg = FilterConfig(exclude_low_price=True, low_price_threshold=0.0)
        errors = cfg.validate()
        assert len(errors) == 1
        assert "low_price_threshold" in errors[0]

    def test_validate_low_price_threshold_negative(self):
        cfg = FilterConfig(exclude_low_price=True, low_price_threshold=-1.0)
        errors = cfg.validate()
        assert len(errors) == 1

    def test_validate_low_price_disabled_ignores_threshold(self):
        # 当 exclude_low_price=False 时，low_price_threshold 校验应跳过
        cfg = FilterConfig(exclude_low_price=False, low_price_threshold=-1.0)
        errors = cfg.validate()
        assert errors == []

    def test_validate_min_pb_negative(self):
        cfg = FilterConfig(min_pb=-0.5)
        errors = cfg.validate()
        assert len(errors) == 1
        assert "min_pb" in errors[0]

    def test_validate_min_pb_zero_is_ok(self):
        cfg = FilterConfig(min_pb=0.0)
        errors = cfg.validate()
        assert errors == []

    def test_validate_min_pb_none_is_ok(self):
        cfg = FilterConfig(min_pb=None)
        errors = cfg.validate()
        assert errors == []

    def test_validate_all_errors(self):
        cfg = FilterConfig(
            min_confidence=-10.0,
            allowed_signals=(),
            exclude_low_price=True,
            low_price_threshold=0.0,
            min_pb=-1.0,
        )
        errors = cfg.validate()
        # 至少应有 4 条错误
        assert len(errors) >= 4

    def test_validate_multiple_valid_signals(self):
        cfg = FilterConfig(allowed_signals=("BUY", "HOLD", "SELL"))
        errors = cfg.validate()
        assert errors == []

    def test_merge_with_range_configs(self):
        cfg = FilterConfig(market_cap_range=None, pe_range=None)
        merged = cfg.merge(
            market_cap_range=(50.0, 5000.0),
            pe_range=(5.0, 30.0),
            pb_range=(0.5, 5.0),
        )
        assert merged.market_cap_range == (50.0, 5000.0)
        assert merged.pe_range == (5.0, 30.0)
        assert merged.pb_range == (0.5, 5.0)
