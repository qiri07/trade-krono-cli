"""测试评分插件系统：三种打分策略、三种风险加分策略、注册表、配置校验、DB 历史记录。"""

from __future__ import annotations

import time

from trade_krono_cli.configs.schema import (
    RiskBoostStrategyConfig,
    ScoringStrategyConfig,
)
from trade_krono_cli.research_db import ResearchDatabase, clear_research_singleton
from trade_krono_cli.scoring import (
    DiminishingBoostBooster,
    FixedBoostBooster,
    LinearScorer,
    MultiplicativeScorer,
    RankBasedScorer,
    ScaledBoostBooster,
    apply_abnormality_risk_boost,
    get_risk_boost_registry,
    get_scorer_registry,
    reset_scoring_registries,
)

# ═══════════════════════════════════════════════════════
# 测试辅助数据
# ═══════════════════════════════════════════════════════


def _make_merged(
    ta_confidence=80.0,
    kronos_change_pct=5.0,
    kronos_direction="UP",
    risk_score_total=20.0,
    uncertainty_confidence=75.0,
    rank=None,
    pool_size=10,
) -> dict:
    """构造最小可用合并结果 dict。"""
    return {
        "ticker": "sh.600519",
        "ta_confidence": ta_confidence,
        "kronos_change_pct": kronos_change_pct,
        "kronos_direction": kronos_direction,
        "risk_score_total": risk_score_total,
        "kronos_prediction_uncertainty": {
            "confidence_score": uncertainty_confidence,
        },
        "rank": rank,
        "_pool_size": pool_size,
    }


# ═══════════════════════════════════════════════════════
# LinearScorer
# ═══════════════════════════════════════════════════════


class TestLinearScorer:
    def test_basic_score(self) -> None:
        """基本打分：中等置信度 + 正向预期 → 合理分数。"""
        s = LinearScorer()
        merged = _make_merged(ta_confidence=70.0, kronos_change_pct=3.0, risk_score_total=30.0)
        score = s.score(merged)
        assert 0 <= score <= 100

    def test_high_confidence_high_change(self) -> None:
        """高置信 + 高预期涨幅 → 高分。"""
        s = LinearScorer()
        merged = _make_merged(
            ta_confidence=95.0,
            kronos_change_pct=15.0,
            kronos_direction="UP",
            risk_score_total=10.0,
        )
        score = s.score(merged)
        assert score > 60

    def test_low_confidence_down_direction(self) -> None:
        """低置信 + 下跌方向 → 低分。"""
        s = LinearScorer()
        merged = _make_merged(
            ta_confidence=30.0,
            kronos_change_pct=-10.0,
            kronos_direction="DOWN",
            risk_score_total=60.0,
        )
        score = s.score(merged)
        assert score < 40

    def test_high_risk_penalty(self) -> None:
        """高风险 → 显著扣分。"""
        s = LinearScorer()
        merged_low_risk = _make_merged(risk_score_total=10.0)
        merged_high_risk = _make_merged(risk_score_total=90.0)
        assert s.score(merged_low_risk) > s.score(merged_high_risk)

    def test_clamp_to_0_100(self) -> None:
        """极端参数下分数仍然在 [0, 100] 范围内。"""
        s = LinearScorer()
        # 极低：低置信 + 大跌 + 高方向惩罚 + 高风险
        merged = _make_merged(
            ta_confidence=5.0,
            kronos_change_pct=-50.0,
            kronos_direction="DOWN",
            risk_score_total=100.0,
        )
        score = s.score(merged)
        assert 0 <= score <= 100

    def test_no_uncertainty(self) -> None:
        """无不确定性数据时不报错。"""
        s = LinearScorer()
        merged = _make_merged(uncertainty_confidence=0.0)
        del merged["kronos_prediction_uncertainty"]
        score = s.score(merged)
        assert 0 <= score <= 100

    def test_name(self) -> None:
        assert LinearScorer.name == "linear"


# ═══════════════════════════════════════════════════════
# MultiplicativeScorer
# ═══════════════════════════════════════════════════════


class TestMultiplicativeScorer:
    def test_risk_sensitive(self) -> None:
        """高风险股票在乘法模型中得分压缩更明显。"""
        base = _make_merged(
            ta_confidence=80.0,
            kronos_change_pct=5.0,
            kronos_direction="UP",
        )
        s = MultiplicativeScorer()
        low_risk = s.score({**base, "risk_score_total": 10.0})
        high_risk = s.score({**base, "risk_score_total": 80.0})
        assert low_risk > high_risk

    def test_linear_equivalent_at_zero_risk(self) -> None:
        """风险分=0 时，乘法模型与线性模型结果一致。"""
        merged = _make_merged(risk_score_total=0.0)
        linear = LinearScorer().score(merged)
        multi = MultiplicativeScorer().score(merged)
        assert abs(linear - multi) < 0.01

    def test_clamp_to_0_100(self) -> None:
        s = MultiplicativeScorer()
        merged = _make_merged(ta_confidence=100.0, kronos_change_pct=50.0, risk_score_total=100.0)
        score = s.score(merged)
        assert 0 <= score <= 100

    def test_name(self) -> None:
        assert MultiplicativeScorer.name == "multiplicative"


# ═══════════════════════════════════════════════════════
# RankBasedScorer
# ═══════════════════════════════════════════════════════


class TestRankBasedScorer:
    def test_rank_one_is_highest(self) -> None:
        """排名 1 应得最高分（接近 100）。"""
        s = RankBasedScorer()
        merged = _make_merged(rank=1, pool_size=10)
        assert s.score(merged) > 90

    def test_rank_last_is_lowest(self) -> None:
        """排名最后应得最低分（接近 0）。"""
        s = RankBasedScorer()
        merged = _make_merged(rank=10, pool_size=10)
        assert s.score(merged) < 10

    def test_fallback_to_linear_when_no_rank(self) -> None:
        """无 rank 信息时 fallback 到 Linear。"""
        s = RankBasedScorer()
        merged = _make_merged(rank=None, pool_size=10)
        # 不应报错，且分数在合理范围内
        score = s.score(merged)
        assert 0 <= score <= 100

    def test_name(self) -> None:
        assert RankBasedScorer.name == "rank_based"


# ═══════════════════════════════════════════════════════
# FixedBoostBooster
# ═══════════════════════════════════════════════════════


class TestFixedBoostBooster:
    def test_single_st_flag(self) -> None:
        """ST 标记增加固定分值。"""
        b = FixedBoostBooster()
        result = b.boost(base_risk=40.0, flags=["ST"])
        # ST 固定加 20 分
        assert result == 60.0

    def test_capped_at_100(self) -> None:
        """加分后上限 100。"""
        b = FixedBoostBooster()
        result = b.boost(base_risk=90.0, flags=["ST", "DELISTED"])
        assert result == 100.0

    def test_no_flags(self) -> None:
        """无标记时不加不减。"""
        b = FixedBoostBooster()
        result = b.boost(base_risk=50.0, flags=[])
        assert result == 50.0

    def test_unknown_flag_ignored(self) -> None:
        """未知标记跳过不计。"""
        b = FixedBoostBooster()
        result = b.boost(base_risk=50.0, flags=["UNKNOWN_FLAG"])
        assert result == 50.0

    def test_name(self) -> None:
        assert FixedBoostBooster.name == "fixed_boost"


# ═══════════════════════════════════════════════════════
# ScaledBoostBooster
# ═══════════════════════════════════════════════════════


class TestScaledBoostBooster:
    def test_multiplier_applied(self) -> None:
        """倍率参数生效：multiplier=2.0 → ST 加 40 分。"""
        b = ScaledBoostBooster()
        result = b.boost(base_risk=40.0, flags=["ST"], params={"multiplier": 2.0})
        assert result == 80.0

    def test_default_multiplier(self) -> None:
        """默认 multiplier=1.0 等价于 fixed_boost。"""
        b = ScaledBoostBooster()
        result = b.boost(base_risk=40.0, flags=["ST"])
        assert result == 60.0

    def test_name(self) -> None:
        assert ScaledBoostBooster.name == "scaled_boost"


# ═══════════════════════════════════════════════════════
# DiminishingBoostBooster
# ═══════════════════════════════════════════════════════


class TestDiminishingBoostBooster:
    def test_single_flag_no_decay(self) -> None:
        """单标记不减幅。"""
        b = DiminishingBoostBooster()
        result = b.boost(base_risk=40.0, flags=["ST"])
        assert result == 60.0

    def test_multiple_flags_diminished(self) -> None:
        """多标记叠加时边际递减。"""
        b = DiminishingBoostBooster()
        # 3 个标记：ST(20) + DELISTED(50) + SUSPENDED(30) = 100 原始
        # 除以 3^(1-0.5) = √3 ≈ 1.732 → 57.7
        result = b.boost(base_risk=0.0, flags=["ST", "DELISTED", "SUSPENDED"])
        assert result < 100.0  # 确认递减
        assert result > 50.0  # 但仍有显著加分

    def test_power_param(self) -> None:
        """power=1.0 时无递减。"""
        b = DiminishingBoostBooster()
        result_full = b.boost(
            base_risk=0.0,
            flags=["ST", "DELISTED"],
            params={"diminishing_power": 1.0},
        )
        result_sqrt = b.boost(
            base_risk=0.0,
            flags=["ST", "DELISTED"],
            params={"diminishing_power": 0.5},
        )
        # power=1.0 时不减（70 原始），power=0.5 时减
        assert result_full == 70.0  # 20+50
        assert result_sqrt < 70.0

    def test_name(self) -> None:
        assert DiminishingBoostBooster.name == "diminishing_boost"


# ═══════════════════════════════════════════════════════
# apply_abnormality_risk_boost (compat wrapper)
# ═══════════════════════════════════════════════════════


class TestApplyAbnormalityRiskBoost:
    def test_default_strategy(self) -> None:
        """默认 fixed_boost 策略。"""
        result = apply_abnormality_risk_boost(40.0, ["ST"])
        assert result == 60.0

    def test_scaled_strategy(self) -> None:
        result = apply_abnormality_risk_boost(
            40.0, ["ST"], strategy="scaled_boost", params={"multiplier": 2.0},
        )
        assert result == 80.0

    def test_disabled(self) -> None:
        """enabled=False 时不调整。"""
        result = apply_abnormality_risk_boost(40.0, ["ST"], enabled=False)
        assert result == 40.0

    def test_unknown_strategy_fallback(self) -> None:
        """未知策略 fallback 到 fixed_boost。"""
        result = apply_abnormality_risk_boost(40.0, ["ST"], strategy="unknown")
        assert result == 60.0


# ═══════════════════════════════════════════════════════
# Registry
# ═══════════════════════════════════════════════════════


class TestScorerRegistry:
    def setup_method(self) -> None:
        reset_scoring_registries()

    def teardown_method(self) -> None:
        reset_scoring_registries()

    def test_get_linear(self) -> None:
        reg = get_scorer_registry()
        scorer = reg.get("linear")
        assert scorer is not None
        assert scorer.name == "linear"

    def test_get_multiplicative(self) -> None:
        reg = get_scorer_registry()
        assert reg.get("multiplicative").name == "multiplicative"

    def test_get_rank_based(self) -> None:
        reg = get_scorer_registry()
        assert reg.get("rank_based").name == "rank_based"

    def test_get_unknown_returns_none(self) -> None:
        reg = get_scorer_registry()
        assert reg.get("nonexistent") is None

    def test_list_all(self) -> None:
        reg = get_scorer_registry()
        names = reg.list_all()
        assert "linear" in names
        assert "multiplicative" in names
        assert "rank_based" in names

    def test_reset_clears_cache(self) -> None:
        reg = get_scorer_registry()
        _ = reg.get("linear")
        reg.reset()
        assert "linear" not in reg._instance_cache

    def test_singleton(self) -> None:
        r1 = get_scorer_registry()
        r2 = get_scorer_registry()
        assert r1 is r2


class TestRiskBoostRegistry:
    def setup_method(self) -> None:
        reset_scoring_registries()

    def teardown_method(self) -> None:
        reset_scoring_registries()

    def test_get_fixed_boost(self) -> None:
        reg = get_risk_boost_registry()
        booster = reg.get("fixed_boost")
        assert booster is not None
        assert booster.name == "fixed_boost"

    def test_get_scaled_boost(self) -> None:
        reg = get_risk_boost_registry()
        assert reg.get("scaled_boost").name == "scaled_boost"

    def test_get_diminishing_boost(self) -> None:
        reg = get_risk_boost_registry()
        assert reg.get("diminishing_boost").name == "diminishing_boost"

    def test_get_unknown_returns_none(self) -> None:
        reg = get_risk_boost_registry()
        assert reg.get("nonexistent") is None

    def test_list_all(self) -> None:
        reg = get_risk_boost_registry()
        names = reg.list_all()
        assert "fixed_boost" in names
        assert "scaled_boost" in names
        assert "diminishing_boost" in names


# ═══════════════════════════════════════════════════════
# Config Validation
# ═══════════════════════════════════════════════════════


class TestScoringStrategyConfig:
    def test_valid_linear(self) -> None:
        cfg = ScoringStrategyConfig(strategy="linear")
        assert cfg.validate() == []

    def test_valid_multiplicative(self) -> None:
        cfg = ScoringStrategyConfig(strategy="multiplicative")
        assert cfg.validate() == []

    def test_valid_rank_based(self) -> None:
        cfg = ScoringStrategyConfig(strategy="rank_based")
        assert cfg.validate() == []

    def test_invalid_strategy(self) -> None:
        cfg = ScoringStrategyConfig(strategy="invalid_xyz")
        errors = cfg.validate()
        assert len(errors) == 1
        assert "invalid_xyz" in errors[0]

    def test_merge(self) -> None:
        cfg = ScoringStrategyConfig(strategy="linear", params={"a": 1})
        merged = cfg.merge(strategy="multiplicative")
        assert merged.strategy == "multiplicative"
        assert merged.params == {"a": 1}


class TestRiskBoostStrategyConfig:
    def test_valid_fixed_boost(self) -> None:
        cfg = RiskBoostStrategyConfig(strategy="fixed_boost")
        assert cfg.validate() == []

    def test_valid_scaled(self) -> None:
        cfg = RiskBoostStrategyConfig(strategy="scaled_boost", multiplier=2.0)
        assert cfg.validate() == []

    def test_valid_diminishing(self) -> None:
        cfg = RiskBoostStrategyConfig(strategy="diminishing_boost", diminishing_power=0.3)
        assert cfg.validate() == []

    def test_invalid_strategy(self) -> None:
        cfg = RiskBoostStrategyConfig(strategy="unknown")
        assert len(cfg.validate()) == 1

    def test_multiplier_out_of_range_low(self) -> None:
        cfg = RiskBoostStrategyConfig(strategy="scaled_boost", multiplier=0.0)
        assert len(cfg.validate()) == 1
        assert "multiplier" in cfg.validate()[0].lower()

    def test_multiplier_out_of_range_high(self) -> None:
        cfg = RiskBoostStrategyConfig(strategy="scaled_boost", multiplier=6.0)
        assert len(cfg.validate()) == 1

    def test_diminishing_power_zero(self) -> None:
        cfg = RiskBoostStrategyConfig(strategy="diminishing_boost", diminishing_power=0.0)
        assert len(cfg.validate()) == 1

    def test_diminishing_power_over_1(self) -> None:
        cfg = RiskBoostStrategyConfig(strategy="diminishing_boost", diminishing_power=1.5)
        assert len(cfg.validate()) == 1

    def test_merge(self) -> None:
        cfg = RiskBoostStrategyConfig(strategy="fixed_boost", multiplier=1.0)
        merged = cfg.merge(strategy="scaled_boost", multiplier=2.0)
        assert merged.strategy == "scaled_boost"
        assert merged.multiplier == 2.0


# ═══════════════════════════════════════════════════════
# PipelineConfig Strategy Integration
# ═══════════════════════════════════════════════════════


class TestPipelineConfigStrategy:
    def test_default_strategy(self) -> None:
        from trade_krono_cli.pipeline_config import PipelineConfig

        cfg = PipelineConfig.default()
        assert cfg.scoring_strategy.strategy == "linear"
        assert cfg.risk_boost_strategy.strategy == "fixed_boost"

    def test_override_scoring_strategy(self) -> None:
        from trade_krono_cli.pipeline_config import PipelineConfig

        cfg = PipelineConfig.default().override(scoring_strategy={"strategy": "multiplicative"})
        assert cfg.scoring_strategy.strategy == "multiplicative"
        assert cfg.risk_boost_strategy.strategy == "fixed_boost"  # 未被影响

    def test_override_risk_boost(self) -> None:
        from trade_krono_cli.pipeline_config import PipelineConfig

        cfg = PipelineConfig.default().override(
            risk_boost_strategy={"strategy": "diminishing_boost", "multiplier": 2.0},
        )
        assert cfg.risk_boost_strategy.strategy == "diminishing_boost"
        assert cfg.risk_boost_strategy.multiplier == 2.0
        assert cfg.scoring_strategy.strategy == "linear"  # 未被影响

    def test_from_dict_strategy(self) -> None:
        from trade_krono_cli.pipeline_config import PipelineConfig

        data = {
            "scoring_strategy": {"strategy": "rank_based"},
            "risk_boost_strategy": {
                "strategy": "scaled_boost",
                "multiplier": 1.5,
                "diminishing_power": 0.7,
            },
        }
        cfg = PipelineConfig.from_dict(data)
        assert cfg.scoring_strategy.strategy == "rank_based"
        assert cfg.risk_boost_strategy.strategy == "scaled_boost"
        assert cfg.risk_boost_strategy.multiplier == 1.5

    def test_to_dict_roundtrip(self) -> None:
        from trade_krono_cli.pipeline_config import PipelineConfig

        cfg = PipelineConfig.default().override(
            scoring_strategy={"strategy": "multiplicative"},
            risk_boost_strategy={"strategy": "diminishing_boost", "multiplier": 2.0},
        )
        d = cfg.to_dict()
        restored = PipelineConfig.from_dict(d)
        assert restored.scoring_strategy.strategy == "multiplicative"
        assert restored.risk_boost_strategy.strategy == "diminishing_boost"
        assert restored.risk_boost_strategy.multiplier == 2.0


# ═══════════════════════════════════════════════════════
# research_db Strategy History
# ═══════════════════════════════════════════════════════


class TestStrategyRunHistory:
    def setup_method(self) -> None:
        clear_research_singleton()

    def teardown_method(self) -> None:
        clear_research_singleton()

    def test_insert_and_query(self, tmp_path) -> None:
        db = ResearchDatabase(db_path=tmp_path / "test_strategy.db")
        run_at = time.time()
        tickers = ["sh.600519", "sz.000001"]
        results = [
            {"ticker": "sh.600519", "composite_score": 85.0},
            {"ticker": "sz.000001", "composite_score": 72.5},
        ]
        run_id = db.insert_strategy_run(
            run_at=run_at,
            strategy="linear",
            params={"ta_confidence_weight": 0.4},
            tickers=tickers,
            results=results,
            notes="test run",
            config_hash="abc123",
        )
        assert run_id > 0

        rows = db.query_strategy_history(strategy="linear")
        assert len(rows) == 1
        assert rows[0]["strategy"] == "linear"
        assert rows[0]["n_results"] == 2
        assert rows[0]["avg_score"] == 78.75
        assert rows[0]["config_hash"] == "abc123"

    def test_query_all(self, tmp_path) -> None:
        db = ResearchDatabase(db_path=tmp_path / "test_strategy_all.db")
        t = time.time()
        db.insert_strategy_run(
            run_at=t, strategy="linear", params={}, tickers=["sh.600519"], results=[],
        )
        db.insert_strategy_run(
            run_at=t + 1, strategy="multiplicative", params={}, tickers=["sz.000001"], results=[],
        )

        all_rows = db.query_strategy_history()
        assert len(all_rows) == 2
        # 按时间倒序
        assert all_rows[0]["strategy"] == "multiplicative"

    def test_query_by_strategy(self, tmp_path) -> None:
        db = ResearchDatabase(db_path=tmp_path / "test_strategy_filter.db")
        t = time.time()
        db.insert_strategy_run(run_at=t, strategy="linear", params={}, tickers=[], results=[])
        db.insert_strategy_run(run_at=t + 1, strategy="linear", params={}, tickers=[], results=[])
        db.insert_strategy_run(
            run_at=t + 2, strategy="multiplicative", params={}, tickers=[], results=[],
        )

        linear_rows = db.query_strategy_history(strategy="linear")
        assert len(linear_rows) == 2
        multi_rows = db.query_strategy_history(strategy="multiplicative")
        assert len(multi_rows) == 1

    def test_limit(self, tmp_path) -> None:
        db = ResearchDatabase(db_path=tmp_path / "test_strategy_limit.db")
        for i in range(5):
            db.insert_strategy_run(
                run_at=time.time() + i,
                strategy="linear",
                params={},
                tickers=[],
                results=[],
            )
        rows = db.query_strategy_history(limit=3)
        assert len(rows) == 3

    def test_stats_includes_strategy_runs(self, tmp_path) -> None:
        db = ResearchDatabase(db_path=tmp_path / "test_strategy_stats.db")
        db.insert_strategy_run(
            run_at=time.time(),
            strategy="linear",
            params={},
            tickers=[],
            results=[],
        )
        stats = db.stats()
        assert stats["research_strategy_runs"] == 1
