"""测试 AbnormalityConfig 配置类。"""

from __future__ import annotations

from trade_krono_cli.configs.abnormality import AbnormalityConfig


class TestAbnormalityConfig:
    def test_default_values(self):
        cfg = AbnormalityConfig()
        assert cfg.skip_new_stock is True
        assert cfg.new_stock_min_days == 60
        assert cfg.kline_min_completeness == 0.85
        assert cfg.abnormality_risk_boost_enabled is True

    def test_custom_values(self):
        cfg = AbnormalityConfig(
            skip_new_stock=False,
            new_stock_min_days=30,
            kline_min_completeness=0.9,
            abnormality_risk_boost_enabled=False,
        )
        assert cfg.skip_new_stock is False
        assert cfg.new_stock_min_days == 30
        assert cfg.kline_min_completeness == 0.9
        assert cfg.abnormality_risk_boost_enabled is False

    def test_merge_override_skip_new_stock(self):
        cfg = AbnormalityConfig()
        merged = cfg.merge(skip_new_stock=False)
        assert merged.skip_new_stock is False
        assert merged.new_stock_min_days == 60  # 未覆盖字段保持默认

    def test_merge_override_min_days(self):
        cfg = AbnormalityConfig(new_stock_min_days=60)
        merged = cfg.merge(new_stock_min_days=120)
        assert merged.new_stock_min_days == 120

    def test_merge_empty_no_change(self):
        cfg = AbnormalityConfig(skip_new_stock=False, new_stock_min_days=90)
        merged = cfg.merge()
        assert merged.skip_new_stock is False
        assert merged.new_stock_min_days == 90

    def test_validate_valid_defaults(self):
        cfg = AbnormalityConfig()
        errors = cfg.validate()
        assert errors == []

    def test_validate_new_stock_min_days_too_low(self):
        cfg = AbnormalityConfig(new_stock_min_days=3)
        errors = cfg.validate()
        assert len(errors) == 1
        assert "new_stock_min_days" in errors[0]

    def test_validate_new_stock_min_days_boundary(self):
        # 最小允许值是 5
        cfg = AbnormalityConfig(new_stock_min_days=5)
        errors = cfg.validate()
        assert errors == []

    def test_validate_completeness_above_one(self):
        cfg = AbnormalityConfig(kline_min_completeness=1.5)
        errors = cfg.validate()
        assert len(errors) == 1
        assert "kline_min_completeness" in errors[0]

    def test_validate_completeness_zero(self):
        cfg = AbnormalityConfig(kline_min_completeness=0.0)
        errors = cfg.validate()
        assert len(errors) == 1
        assert "kline_min_completeness" in errors[0]

    def test_validate_completeness_negative(self):
        cfg = AbnormalityConfig(kline_min_completeness=-0.1)
        errors = cfg.validate()
        assert len(errors) == 1

    def test_validate_completeness_valid_boundary(self):
        cfg = AbnormalityConfig(kline_min_completeness=1.0)
        errors = cfg.validate()
        assert errors == []

    def test_validate_all_errors(self):
        cfg = AbnormalityConfig(new_stock_min_days=1, kline_min_completeness=0.0)
        errors = cfg.validate()
        assert len(errors) == 2

    def test_merge_completeness(self):
        cfg = AbnormalityConfig(kline_min_completeness=0.85)
        merged = cfg.merge(kline_min_completeness=0.95)
        assert merged.kline_min_completeness == 0.95
        assert merged.new_stock_min_days == 60  # 未覆盖保持默认
