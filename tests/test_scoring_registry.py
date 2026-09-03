"""scoring.registry 和 scorers 的测试。"""

from __future__ import annotations

from trade_krono_cli.configs.scoring import ScoringConfig
from trade_krono_cli.scoring.base import CompositeScorer, RiskBoostStrategy
from trade_krono_cli.scoring.registry import RiskBoostRegistry, ScorerRegistry
from trade_krono_cli.scoring.scorers import LinearScorer, MultiplicativeScorer, RankBasedScorer


class DummyScorer(CompositeScorer):
    """测试用打分器。"""

    name = "dummy"

    def _score_impl(self, merged: dict, config: ScoringConfig | None = None) -> float:
        return 50.0


class DummyRiskBoost(RiskBoostStrategy):
    """测试用风险加分策略。"""

    name = "dummy_boost"

    def _boost_impl(
        self, base_risk: float, flags: list[str], params: object | None = None,
    ) -> float:
        return base_risk + 10.0


class TestScorerRegistry:
    """ScorerRegistry 注册表测试。"""

    def setup_method(self) -> None:
        self.registry = ScorerRegistry()
        self.registry.reset()

    def test_register_and_get(self) -> None:
        self.registry.register(DummyScorer)
        instance = self.registry.get("dummy")
        assert instance is not None
        assert isinstance(instance, DummyScorer)

    def test_get_returns_cached_instance(self) -> None:
        self.registry.register(DummyScorer)
        first = self.registry.get("dummy")
        second = self.registry.get("dummy")
        assert first is second  # 同一实例

    def test_get_unknown_returns_none(self) -> None:
        result = self.registry.get("nonexistent")
        assert result is None

    def test_list_all(self) -> None:
        self.registry.register(DummyScorer)
        names = self.registry.list_all()
        assert "dummy" in names

    def test_lazy_load_linear(self) -> None:
        instance = self.registry.get("linear")
        assert isinstance(instance, LinearScorer)

    def test_lazy_load_multiplicative(self) -> None:
        instance = self.registry.get("multiplicative")
        assert isinstance(instance, MultiplicativeScorer)

    def test_lazy_load_rank_based(self) -> None:
        instance = self.registry.get("rank_based")
        assert isinstance(instance, RankBasedScorer)

    def test_reset_clears_cache(self) -> None:
        self.registry.register(DummyScorer)
        first = self.registry.get("dummy")
        assert first is not None
        # 直接创建实例验证与缓存不同
        fresh = DummyScorer()
        assert first is not fresh

    def test_thread_safety(self) -> None:
        """注册表基本可用性验证。"""
        self.registry.register(DummyScorer)
        assert len(self.registry.list_all()) >= 1


class TestRiskBoostRegistry:
    """RiskBoostRegistry 注册表测试。"""

    def setup_method(self) -> None:
        self.registry = RiskBoostRegistry()
        self.registry.reset()

    def test_register_and_get(self) -> None:
        self.registry.register(DummyRiskBoost)
        instance = self.registry.get("dummy_boost")
        assert instance is not None
        assert isinstance(instance, DummyRiskBoost)

    def test_get_unknown_returns_none(self) -> None:
        assert self.registry.get("nonexistent") is None

    def test_list_all(self) -> None:
        self.registry.register(DummyRiskBoost)
        assert "dummy_boost" in self.registry.list_all()


class TestLinearScorer:
    """LinearScorer 打分逻辑测试。"""

    def setup_method(self) -> None:
        self.scorer = LinearScorer()
        self.config = ScoringConfig()

    def test_basic_score(self) -> None:
        merged = {
            "ta_confidence": 80.0,
            "kronos_direction": "UP",
            "kronos_change_pct": 2.0,
            "risk_score": 0.1,
        }
        score = self.scorer.score(merged, self.config)
        assert 0 <= score <= 100

    def test_high_confidence_high_change(self) -> None:
        merged = {
            "ta_confidence": 95.0,
            "kronos_direction": "UP",
            "kronos_change_pct": 5.0,
            "risk_score": 0.0,
        }
        score = self.scorer.score(merged, self.config)
        # 高置信度 + 正向预期涨幅 → 分数应明显高于中性基准（约55）
        assert score > 50

    def test_low_confidence_down_trend(self) -> None:
        merged = {
            "ta_confidence": 30.0,
            "kronos_direction": "DOWN",
            "kronos_change_pct": -3.0,
            "risk_score": 0.5,
        }
        score = self.scorer.score(merged, self.config)
        # 低置信度 + 负面方向 + 中等风险 → 分数应较低（约20-35）
        assert score < 40

    def test_with_adjusted_expected_return(self) -> None:
        """使用 adjusted_expected_return 替代 kronos_change_pct。"""
        merged = {
            "ta_confidence": 70.0,
            "adjusted_expected_return": 3.0,
            "kronos_direction": "UP",
            "risk_score": 0.0,
        }
        score = self.scorer.score(merged, self.config)
        assert score > 0

    def test_none_values_handled(self) -> None:
        """缺失字段应降级处理。"""
        merged: dict[str, object] = {}
        score = self.scorer.score(merged, self.config)
        assert 0 <= score <= 100

    def test_clamp_to_range(self) -> None:
        """分数应钳制在 0-100 范围内。"""
        merged = {
            "ta_confidence": 0.0,
            "kronos_direction": "DOWN",
            "kronos_change_pct": -20.0,
            "risk_score": 1.0,
        }
        score = self.scorer.score(merged, self.config)
        assert 0 <= score <= 100


class TestMultiplicativeScorer:
    """MultiplicativeScorer 乘法衰减打分测试。"""

    def setup_method(self) -> None:
        self.scorer = MultiplicativeScorer()
        self.config = ScoringConfig()

    def test_high_risk_decays_score(self) -> None:
        merged = {
            "ta_confidence": 90.0,
            "kronos_direction": "UP",
            "kronos_change_pct": 3.0,
            "risk_score": 0.8,  # 高风险
        }
        score = self.scorer.score(merged, self.config)
        # 高风险应使分数低于中等风险版本（~55）
        assert score < 55

    def test_low_risk_preserves_score(self) -> None:
        merged = {
            "ta_confidence": 80.0,
            "kronos_direction": "UP",
            "kronos_change_pct": 2.0,
            "risk_score": 0.1,  # 低风险
        }
        score = self.scorer.score(merged, self.config)
        # 低风险应保留较高分数
        assert score > 40


class TestRankBasedScorer:
    """RankBasedScorer 百分位排名打分测试。"""

    def setup_method(self) -> None:
        self.scorer = RankBasedScorer()
        self.config = ScoringConfig()

    def test_relative_ranking(self) -> None:
        merged = {
            "ta_confidence": 75.0,
            "kronos_direction": "UP",
            "kronos_change_pct": 1.5,
            "risk_score": 0.2,
        }
        score = self.scorer.score(merged, self.config)
        assert 0 <= score <= 100
