"""测试 scoring.base — 抽象基类（CompositeScorer、RiskBoostStrategy、RatingMapper）
及注册表 reset 行为。
"""

from __future__ import annotations

import pytest

from trade_krono_cli.scoring.base import (
    BoostResult,
    CompositeScorer,
    RatingMapper,
    RiskBoostStrategy,
    ScoreResult,
)
from trade_krono_cli.scoring.registry import (
    RiskBoostRegistry,
    ScorerRegistry,
    get_risk_boost_registry,
    get_scorer_registry,
    reset_scoring_registries,
)
from trade_krono_cli.ta_decision import Signal

# ═══════════════════════════════════════════════════════
# 测试辅助：具体实现（供 ABC 行为测试使用）
# ═══════════════════════════════════════════════════════


class _DummyScorer(CompositeScorer):
    """用于测试 ABC 行为的虚拟打分器。"""

    name = "dummy_scorer"

    def _score_impl(self, merged: dict, config=None) -> float:
        return 42.0


class _BadScorer(CompositeScorer):
    """抛出异常的打分器，用于测试错误处理。"""

    name = "bad_scorer"

    def _score_impl(self, merged: dict, config=None) -> float:
        msg = "scoring failed"
        raise RuntimeError(msg)


class _FixedBooster(RiskBoostStrategy):
    """用于测试 ABC 行为的虚拟风险加分策略。"""

    name = "fixed_booster_test"

    def _boost_impl(self, base_risk: float, flags: list[str], params=None) -> float:
        return base_risk + 10.0


class _TextToBuyMapper(RatingMapper):
    """简单的 RatingMapper 实现：所有输入映射为 BUY, 80.0。"""

    name = "text_to_buy"

    def map_rating(self, rating_text: str) -> tuple[Signal, float]:
        return Signal.BUY, 80.0


class _TextToSellMapper(RatingMapper):
    """简单的 RatingMapper 实现：所有输入映射为 SELL, 30.0。"""

    name = "text_to_sell"

    def map_rating(self, rating_text: str) -> tuple[Signal, float]:
        if "buy" in rating_text.lower():
            return Signal.BUY, 90.0
        if "sell" in rating_text.lower():
            return Signal.SELL, 20.0
        return Signal.HOLD, 50.0


# ═══════════════════════════════════════════════════════
# ScoreResult / BoostResult 数据类测试
# ═══════════════════════════════════════════════════════


class TestScoreResult:
    def test_default_values(self) -> None:
        r = ScoreResult(score=75.0)
        assert r.score == 75.0
        assert r.raw_components == {}
        assert r.strategy_name == ""

    def test_with_components(self) -> None:
        r = ScoreResult(
            score=80.0,
            raw_components={"ta": 85.0, "kronos": 75.0},
            strategy_name="linear",
        )
        assert r.raw_components == {"ta": 85.0, "kronos": 75.0}
        assert r.strategy_name == "linear"

    def test_frozen(self) -> None:
        """ScoreResult 是 frozen dataclass，不可修改。"""
        r = ScoreResult(score=50.0)
        with pytest.raises(AttributeError):
            r.score = 60.0


class TestBoostResult:
    def test_default_values(self) -> None:
        r = BoostResult(boosted_risk=50.0, base_risk=40.0, total_boost=10.0)
        assert r.flags_applied == []

    def test_with_flags(self) -> None:
        r = BoostResult(
            boosted_risk=70.0, base_risk=40.0, total_boost=30.0, flags_applied=["ST", "SUSPENDED"],
        )
        assert r.flags_applied == ["ST", "SUSPENDED"]

    def test_frozen(self) -> None:
        r = BoostResult(boosted_risk=50.0, base_risk=40.0, total_boost=10.0)
        with pytest.raises(AttributeError):
            r.boosted_risk = 60.0


# ═══════════════════════════════════════════════════════
# CompositeScorer ABC 测试
# ═══════════════════════════════════════════════════════


class TestCompositeScorerABC:
    def test_cannot_instantiate_abstract(self) -> None:
        with pytest.raises(TypeError):
            CompositeScorer()

    def test_subclass_without_impl_raises(self) -> None:
        class NoImpl(CompositeScorer):
            pass

        with pytest.raises(TypeError):
            NoImpl()

    def test_concrete_impl_scores(self) -> None:
        scorer = _DummyScorer()
        assert scorer.score({"ticker": "sh.600519"}) == 42.0

    def test_concrete_impl_describe(self) -> None:
        scorer = _DummyScorer()
        assert scorer.describe() == "CompositeScorer[dummy_scorer]"

    def test_error_in_score_impl_propagates(self) -> None:
        scorer = _BadScorer()
        with pytest.raises(RuntimeError, match="scoring failed"):
            scorer.score({})

    def test_config_passed_through(self) -> None:
        """Config 参数应原样传入 _score_impl。"""
        received = {}

        class CaptureScorer(CompositeScorer):
            name = "capture"

            def _score_impl(self, merged, config=None) -> float:
                received["config"] = config
                return 0.0

        cfg = {"threshold": 0.8}
        CaptureScorer().score({}, config=cfg)
        assert received["config"] == cfg


# ═══════════════════════════════════════════════════════
# RiskBoostStrategy ABC 测试
# ═══════════════════════════════════════════════════════


class TestRiskBoostStrategyABC:
    def test_cannot_instantiate_abstract(self) -> None:
        with pytest.raises(TypeError):
            RiskBoostStrategy()

    def test_subclass_without_impl_raises(self) -> None:
        class NoImpl(RiskBoostStrategy):
            pass

        with pytest.raises(TypeError):
            NoImpl()

    def test_concrete_impl_boosts(self) -> None:
        booster = _FixedBooster()
        assert booster.boost(base_risk=30.0, flags=["ST"]) == 40.0

    def test_concrete_impl_describe(self) -> None:
        booster = _FixedBooster()
        assert booster.describe() == "RiskBoostStrategy[fixed_booster_test]"

    def test_boost_returns_float_from_result(self) -> None:
        """返回 BoostResult 时，boost() 应提取 boosted_risk。"""

        class ResultBooster(RiskBoostStrategy):
            name = "result_booster"

            def _boost_impl(self, base_risk, flags, params=None):
                return BoostResult(boosted_risk=99.0, base_risk=base_risk, total_boost=69.0)

        result = ResultBooster().boost(base_risk=30.0, flags=[])
        assert result == 99.0


# ═══════════════════════════════════════════════════════
# RatingMapper ABC 测试
# ═══════════════════════════════════════════════════════


class TestRatingMapperABC:
    def test_cannot_instantiate_abstract(self) -> None:
        with pytest.raises(TypeError):
            RatingMapper()

    def test_subclass_without_impl_raises(self) -> None:
        class NoImpl(RatingMapper):
            pass

        with pytest.raises(TypeError):
            NoImpl()

    def test_map_buy_text(self) -> None:
        mapper = _TextToBuyMapper()
        signal, confidence = mapper.map_rating("strong buy")
        assert signal == Signal.BUY
        assert confidence == 80.0

    def test_map_sell_text(self) -> None:
        mapper = _TextToSellMapper()
        s1, c1 = mapper.map_rating("this is a buy opportunity")
        s2, c2 = mapper.map_rating("definitely sell now")
        s3, c3 = mapper.map_rating("hold steady")
        assert s1 == Signal.BUY
        assert c1 == 90.0
        assert s2 == Signal.SELL
        assert c2 == 20.0
        assert s3 == Signal.HOLD
        assert c3 == 50.0

    def test_map_chinese_text(self) -> None:
        """支持中文评级文本。"""

        class ChineseMapper(RatingMapper):
            name = "chinese_mapper"

            def map_rating(self, rating_text: str) -> tuple[Signal, float]:
                if "买入" in rating_text:
                    return Signal.BUY, 85.0
                if "卖出" in rating_text:
                    return Signal.SELL, 25.0
                return Signal.HOLD, 55.0

        mapper = ChineseMapper()
        s, c = mapper.map_rating("强烈建议买入")
        assert s == Signal.BUY
        assert c == 85.0
        s, c = mapper.map_rating("建议持有观望")
        assert s == Signal.HOLD
        assert c == 55.0

    def test_describe(self) -> None:
        mapper = _TextToBuyMapper()
        assert mapper.describe() == "RatingMapper[text_to_buy]"

    def test_empty_string_returns_default(self) -> None:
        """空字符串应返回默认映射。"""
        mapper = _TextToBuyMapper()
        s, c = mapper.map_rating("")
        assert s == Signal.BUY
        assert c == 80.0


# ═══════════════════════════════════════════════════════
# ScorerRegistry 测试
# ═══════════════════════════════════════════════════════


class TestScorerRegistry:
    def setup_method(self) -> None:
        reset_scoring_registries()

    def teardown_method(self) -> None:
        reset_scoring_registries()

    def test_builtin_strategies_loaded(self) -> None:
        registry = get_scorer_registry()
        builtins = registry.list_all()
        assert "linear" in builtins
        assert "multiplicative" in builtins
        assert "rank_based" in builtins

    def test_get_builtin_linear(self) -> None:
        from trade_krono_cli.scoring.scorers import LinearScorer

        registry = get_scorer_registry()
        scorer = registry.get("linear")
        assert isinstance(scorer, LinearScorer)

    def test_get_unknown_returns_none(self) -> None:
        registry = get_scorer_registry()
        assert registry.get("nonexistent_strategy") is None

    def test_register_and_get(self) -> None:
        registry = ScorerRegistry()
        registry.register(_DummyScorer)
        instance = registry.get("dummy_scorer")
        assert isinstance(instance, _DummyScorer)
        assert instance.score({}) == 42.0

    def test_cache_returns_same_instance(self) -> None:
        registry = ScorerRegistry()
        registry.register(_DummyScorer)
        inst1 = registry.get("dummy_scorer")
        inst2 = registry.get("dummy_scorer")
        assert inst1 is inst2  # 同一缓存实例

    def test_reset_clears_cache(self) -> None:
        registry = ScorerRegistry()
        registry.register(_DummyScorer)
        inst = registry.get("dummy_scorer")
        assert inst is not None

        registry.reset()
        inst2 = registry.get("dummy_scorer")
        assert inst is not inst2  # 重置后得到新实例

    def test_reset_via_module_function(self) -> None:
        """reset_scoring_registries() 应清空所有缓存。"""
        registry = get_scorer_registry()
        _ = registry.get("linear")  # 触发缓存

        reset_scoring_registries()

        # 内置策略应在下次 get 时重新懒加载
        new_registry = get_scorer_registry()
        assert new_registry.get("linear") is not None


# ═══════════════════════════════════════════════════════
# RiskBoostRegistry 测试
# ═══════════════════════════════════════════════════════


class TestRiskBoostRegistry:
    def setup_method(self) -> None:
        reset_scoring_registries()

    def teardown_method(self) -> None:
        reset_scoring_registries()

    def test_builtin_strategies_loaded(self) -> None:
        registry = get_risk_boost_registry()
        builtins = registry.list_all()
        assert "fixed_boost" in builtins
        assert "scaled_boost" in builtins
        assert "diminishing_boost" in builtins

    def test_get_fixed_boost(self) -> None:
        from trade_krono_cli.scoring.risk_boosters import FixedBoostBooster

        registry = get_risk_boost_registry()
        booster = registry.get("fixed_boost")
        assert isinstance(booster, FixedBoostBooster)

    def test_get_unknown_returns_none(self) -> None:
        registry = get_risk_boost_registry()
        assert registry.get("nonexistent_booster") is None

    def test_register_and_get(self) -> None:
        registry = RiskBoostRegistry()
        registry.register(_FixedBooster)
        instance = registry.get("fixed_booster_test")
        assert isinstance(instance, _FixedBooster)
        assert instance.boost(base_risk=30.0, flags=[]) == 40.0

    def test_reset_clears_cache(self) -> None:
        registry = RiskBoostRegistry()
        registry.register(_FixedBooster)
        inst = registry.get("fixed_booster_test")
        assert inst is not None

        registry.reset()
        inst2 = registry.get("fixed_booster_test")
        assert inst is not inst2
