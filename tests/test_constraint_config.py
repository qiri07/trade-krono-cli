"""ConstraintConfig 测试 — maker/taker 手续费区分。"""

from __future__ import annotations

import pytest

from trade_krono_cli.configs.trading import ConstraintConfig


class TestConstraintConfig:
    """测试交易约束配置。"""

    def test_default_values(self) -> None:
        """默认值测试。"""
        cfg = ConstraintConfig()
        assert cfg.commission_bps == 3.0
        assert cfg.slippage_bps == 5.0
        assert cfg.stamp_duty_bps == 1.0
        assert cfg.enable_limit_check is True
        assert cfg.enable_t1 is True

    def test_buy_cost_bps(self) -> None:
        """买入成本 = 佣金 + 滑点。"""
        cfg = ConstraintConfig()
        assert cfg.buy_cost_bps() == 8.0  # 3 + 5

    def test_sell_cost_bps(self) -> None:
        """卖出成本 = 佣金 + 滑点 + 印花税。"""
        cfg = ConstraintConfig()
        assert cfg.sell_cost_bps() == 9.0  # 3 + 5 + 1

    def test_total_roundtrip_bps(self) -> None:
        """双边成本 = 买入成本 + 卖出成本。"""
        cfg = ConstraintConfig()
        assert cfg.total_roundtrip_bps() == 17.0  # 8 + 9

    def test_maker_taker_different(self) -> None:
        """maker/taker 费用可区分。"""
        cfg = ConstraintConfig(
            commission_bps=3.0,
            slippage_bps=5.0,
            maker_commission_bps=1.0,  # 挂单便宜
            taker_commission_bps=5.0,  # 吃单贵
            maker_slippage_bps=2.0,
            taker_slippage_bps=8.0,
        )
        # maker 买入成本 = 1 + 2 = 3 bps
        assert cfg.buy_cost_bps_maker() == 3.0
        # taker 买入成本 = 5 + 8 = 13 bps
        assert cfg.buy_cost_bps() == 13.0

    def test_apply_cost(self) -> None:
        """测试成本扣减。"""
        cfg = ConstraintConfig(enable_cost_model=True)
        result = cfg.apply_cost(10.0, order_type="taker")
        # 10% - 8bps = 10 - 0.08 = 9.92
        assert result == pytest.approx(9.92, abs=0.01)

    def test_apply_roundtrip_cost(self) -> None:
        """测试双边成本扣减。"""
        cfg = ConstraintConfig(enable_cost_model=True)
        result = cfg.apply_roundtrip_cost(10.0, order_type="taker")
        # 10% - 17bps = 10 - 0.17 = 9.83
        assert result == pytest.approx(9.83, abs=0.01)

    def test_cost_model_disabled(self) -> None:
        """关闭成本模型时不应扣减。"""
        cfg = ConstraintConfig(enable_cost_model=False)
        result = cfg.apply_cost(10.0)
        assert result == 10.0

    def test_validate_negative(self) -> None:
        """负值费用应报错。"""
        cfg = ConstraintConfig(commission_bps=-1.0)
        errors = cfg.validate()
        assert any("commission_bps" in e for e in errors)

    def test_merge(self) -> None:
        """测试 merge 方法。"""
        cfg = ConstraintConfig(commission_bps=3.0)
        merged = cfg.merge(commission_bps=5.0, slippage_bps=10.0)
        assert merged.commission_bps == 5.0
        assert merged.slippage_bps == 10.0
        # 其他字段保持不变
        assert merged.stamp_duty_bps == 1.0
