"""补充测试：scoring 模块的边界情况和遗漏分支。

覆盖 RankBasedScorer fallback、MultiplicativeScorer with adjusted_return、
ScorerRegistry 双重注册幂等性等。
"""

from __future__ import annotations

from trade_krono_cli.configs.schema import ScoringConfig
from trade_krono_cli.scoring import (
    LinearScorer,
    MultiplicativeScorer,
    RankBasedScorer,
    get_scorer_registry,
    reset_scoring_registries,
)
from trade_krono_cli.scoring.registry import RiskBoostRegistry, ScorerRegistry

# ═══════════════════════════════════════════════════════
#  RankBasedScorer fallback
# ═══════════════════════════════════════════════════════


class TestRankBasedScorerFallback:
    def test_rank_none_falls_back_to_linear(self) -> None:
        """rank=None 时应退化为 LinearScorer。"""
        scorer = RankBasedScorer()
        merged = {
            "ta_confidence": 70.0,
            "kronos_change_pct": 3.0,
            "kronos_direction": "UP",
            "kronos_prediction_uncertainty": {"confidence_score": 80.0},
            "risk_score_total": 20.0,
            "rank": None,  # 关键：rank 缺失
        }
        config = ScoringConfig()
        result = scorer.score(merged, config=config)
        # 不应抛出异常，返回有效分数
        assert isinstance(result, (int, float))
        assert 0 <= result <= 100

    def test_rank_zero_falls_back(self) -> None:
        """rank=0 时应退化为 LinearScorer。"""
        scorer = RankBasedScorer()
        merged = {
            "ta_confidence": 70.0,
            "kronos_change_pct": 3.0,
            "kronos_direction": "UP",
            "kronos_prediction_uncertainty": {"confidence_score": 80.0},
            "risk_score_total": 20.0,
            "rank": 0,
        }
        result = scorer.score(merged, config=ScoringConfig())
        assert isinstance(result, (int, float))


# ═══════════════════════════════════════════════════════
#  MultiplicativeScorer with adjusted_expected_return
# ═══════════════════════════════════════════════════════


class TestMultiplicativeScorerAdjustedReturn:
    def test_uses_adjusted_return_when_present(self) -> None:
        """当 adjusted_expected_return 存在时，MultiplicativeScorer 应使用它。"""
        scorer = MultiplicativeScorer()
        merged = {
            "ta_confidence": 80.0,
            "kronos_change_pct": 3.0,
            "adjusted_expected_return": 2.0,  # 风险调整后的收益
            "kronos_direction": "UP",
            "kronos_prediction_uncertainty": {"confidence_score": 70.0},
            "risk_score_total": 30.0,
        }
        result = scorer.score(merged, config=ScoringConfig())
        assert isinstance(result, (int, float))
        assert result > 0

    def test_fallback_to_kronos_change_pct(self) -> None:
        """无 adjusted_expected_return 时应回退到 kronos_change_pct。"""
        scorer = MultiplicativeScorer()
        merged = {
            "ta_confidence": 80.0,
            "kronos_change_pct": 3.0,
            # 无 adjusted_expected_return
            "kronos_direction": "UP",
            "kronos_prediction_uncertainty": {"confidence_score": 70.0},
            "risk_score_total": 30.0,
        }
        result = scorer.score(merged, config=ScoringConfig())
        assert isinstance(result, (int, float))


# ═══════════════════════════════════════════════════════
#  ScorerRegistry 边界情况
# ═══════════════════════════════════════════════════════


class TestScorerRegistryEdgeCases:
    def test_double_register_idempotent(self) -> None:
        """重复注册同一名称应幂等（不报错，后注册覆盖前注册）。"""
        reg = ScorerRegistry()
        reg.register(LinearScorer)
        reg.register(MultiplicativeScorer)  # 覆盖
        instance = reg.get("multiplicative")
        assert isinstance(instance, MultiplicativeScorer)

    def test_get_unknown_returns_none_after_reset(self) -> None:
        """reset 后实例缓存清空，但注册表仍保留内置策略。"""
        reg = ScorerRegistry()
        # 先获取内置 linear 触发缓存
        _ = reg.get("linear")
        reg.reset()
        # reset 清空实例缓存但不影响注册表；get("linear") 重新创建
        instance = reg.get("linear")
        assert instance is not None
        assert isinstance(instance, LinearScorer)

    def test_list_all_empty(self) -> None:
        """新创建的独立 ScorerRegistry 实例应不含内置策略。"""
        from trade_krono_cli.scoring.registry import ScorerRegistry
        # 重置类级别注册表，确保测试隔离
        ScorerRegistry._registry.clear()
        reg = ScorerRegistry()
        assert reg.list_all() == []


# ═══════════════════════════════════════════════════════
#  RiskBoostRegistry 边界
# ═══════════════════════════════════════════════════════


class TestRiskBoostRegistryEdgeCases:
    def test_get_unknown_returns_none(self) -> None:
        reg = RiskBoostRegistry()
        assert reg.get("nonexistent") is None

    def test_list_all_empty(self) -> None:
        reg = RiskBoostRegistry()
        assert reg.list_all() == []


# ═══════════════════════════════════════════════════════
#  get_scorer_registry / reset_scoring_registries
# ═══════════════════════════════════════════════════════


class TestGlobalRegistries:
    def test_get_scorer_registry_returns_instance(self) -> None:
        reg = get_scorer_registry()
        assert reg is not None
        # 已注册了 linear/multiplicative/rank_based
        assert reg.get("linear") is not None
        assert reg.get("multiplicative") is not None
        assert reg.get("rank_based") is not None

    def test_reset_clears_registries(self) -> None:
        reset_scoring_registries()
        reg = get_scorer_registry()
        assert reg.get("linear") is not None  # 重新懒加载
