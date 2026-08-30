"""
测试 scoring.risk_boosters — 三种异常标记风险加分策略。
"""

from __future__ import annotations

import pytest

from trade_krono_cli.scoring.risk_boosters import (
    DiminishingBoostBooster,
    FixedBoostBooster,
    ScaledBoostBooster,
)

# ── FixedBoostBooster ─────────────────────────────────────────────────────────


class TestFixedBoostBooster:
    """固定值叠加型风险加分策略测试。"""

    @pytest.fixture
    def booster(self) -> FixedBoostBooster:
        return FixedBoostBooster()

    def test_no_flags(self, booster: FixedBoostBooster):
        """无异常标记时风险不变。"""
        result = booster.boost(base_risk=30.0, flags=[], params={})
        assert result == pytest.approx(30.0)

    def test_single_flag(self, booster: FixedBoostBooster):
        """单个异常标记应按固定值加分。"""
        result = booster.boost(base_risk=30.0, flags=["ST"], params={})
        # ST 标记固定加分为 20
        assert result == pytest.approx(50.0)

    def test_multiple_flags_accumulate(self, booster: FixedBoostBooster):
        """多个异常标记应累加。"""
        result = booster.boost(base_risk=20.0, flags=["ST", "NEW_STOCK"], params={})
        # ST=20, NEW_STOCK=10 → 总共加30
        assert result == pytest.approx(50.0)

    def test_cap_at_100(self, booster: FixedBoostBooster):
        """加分后不应超过 100。"""
        result = booster.boost(base_risk=90.0, flags=["ST", "NEW_STOCK", "DELISTED"], params={})
        # 90 + 20 + 10 + 50 = 170 → cap to 100
        assert result == pytest.approx(100.0)

    def test_unknown_flag_skipped(self, booster: FixedBoostBooster):
        """未知标记应跳过，不影响其他标记。"""
        result = booster.boost(base_risk=30.0, flags=["ST", "UNKNOWN_FLAG"], params={})
        assert result == pytest.approx(50.0)

    def test_custom_multiplier(self, booster: FixedBoostBooster):
        """可通过 params 指定倍率。"""
        result = booster.boost(base_risk=30.0, flags=["ST"], params={"multiplier": 2.0})
        # ST=20 * 2.0 = 40
        assert result == pytest.approx(70.0)

    def test_name(self, booster: FixedBoostBooster):
        assert booster.name == "fixed_boost"


# ── ScaledBoostBooster ────────────────────────────────────────────────────────


class TestScaledBoostBooster:
    """缩放型风险加分策略测试。"""

    @pytest.fixture
    def booster(self) -> ScaledBoostBooster:
        return ScaledBoostBooster()

    def test_default_multiplier(self, booster: ScaledBoostBooster):
        """默认倍率为 1.0，行为与 fixed_boost 一致。"""
        result = booster.boost(base_risk=30.0, flags=["ST"], params={})
        assert result == pytest.approx(50.0)

    def test_mild_multiplier(self, booster: ScaledBoostBooster):
        """温和倍率 0.5 → 减半加分。"""
        result = booster.boost(base_risk=30.0, flags=["ST"], params={"multiplier": 0.5})
        # ST=20 * 0.5 = 10
        assert result == pytest.approx(40.0)

    def test_aggressive_multiplier(self, booster: ScaledBoostBooster):
        """激进倍率 2.0 → 加倍加分。"""
        result = booster.boost(base_risk=30.0, flags=["ST"], params={"multiplier": 2.0})
        # ST=20 * 2.0 = 40
        assert result == pytest.approx(70.0)

    def test_name(self, booster: ScaledBoostBooster):
        assert booster.name == "scaled_boost"


# ── DiminishingBoostBooster ──────────────────────────────────────────────────


class TestDiminishingBoostBooster:
    """边际递减型风险加分策略测试。"""

    @pytest.fixture
    def booster(self) -> DiminishingBoostBooster:
        return DiminishingBoostBooster()

    def test_single_flag(self, booster: DiminishingBoostBooster):
        """单个标记应正常加分。"""
        result = booster.boost(base_risk=30.0, flags=["ST"], params={})
        assert result == pytest.approx(50.0)

    def test_multiple_flags_diminishing(self, booster: DiminishingBoostBooster):
        """多个标记应平方根缩放，防止过度惩罚。"""
        result = booster.boost(base_risk=30.0, flags=["ST", "NEW_STOCK", "DELISTED"], params={})
        # 固定叠加: 20+10+50=80, 边际递减应小于80
        assert result < 110.0  # 30 + 80 = 110 是固定叠加的上限
        assert result <= 100.0  # cap at 100

    def test_name(self, booster: DiminishingBoostBooster):
        assert booster.name == "diminishing_boost"

    def test_cap_at_100(self, booster: DiminishingBoostBooster):
        """加分后不应超过 100。"""
        result = booster.boost(base_risk=95.0, flags=["ST", "NEW_STOCK"], params={})
        assert result <= 100.0


# ── 策略注册与获取 ───────────────────────────────────────────────────────────


class TestRiskBoostStrategyRegistry:
    """策略注册表测试。"""

    def test_fixed_boost_registered(self):
        """fixed_boost 策略应可获取。"""
        from trade_krono_cli.scoring.registry import get_risk_boost_registry

        registry = get_risk_boost_registry()
        booster = registry.get("fixed_boost")
        assert booster is not None
        assert isinstance(booster, FixedBoostBooster)

    def test_scaled_boost_registered(self):
        """scaled_boost 策略应可获取。"""
        from trade_krono_cli.scoring.registry import get_risk_boost_registry

        registry = get_risk_boost_registry()
        booster = registry.get("scaled_boost")
        assert booster is not None
        assert isinstance(booster, ScaledBoostBooster)

    def test_diminishing_boost_registered(self):
        """diminishing_boost 策略应可获取。"""
        from trade_krono_cli.scoring.registry import get_risk_boost_registry

        registry = get_risk_boost_registry()
        booster = registry.get("diminishing_boost")
        assert booster is not None
        assert isinstance(booster, DiminishingBoostBooster)

    def test_unknown_strategy_returns_none(self):
        """未知策略名应返回 None。"""
        from trade_krono_cli.scoring.registry import get_risk_boost_registry

        registry = get_risk_boost_registry()
        result = registry.get("unknown_strategy")
        assert result is None
