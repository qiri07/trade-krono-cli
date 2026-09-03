"""性能基准测试：监控核心路径的性能退化。

手动计时实现，无需外部 pytest-benchmark 依赖。
运行方式：pytest tests/test_benchmarks.py -v
"""

import time

from trade_krono_cli.kronos_runner import KronosForecastResult
from trade_krono_cli.ta_runner import StockAnalysisResult


def benchmark(fn, iterations=100):
    """通用计时辅助：执行 iterations 次并返回平均耗时（ms）。"""
    # warmup
    for _ in range(5):
        fn()
    # measure
    times = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        fn()
        times.append(time.perf_counter() - t0)
    return (sum(times) / len(times)) * 1000


# ═══════════════════════════════════════════════════════
# 辅助：构造合并结果数据
# ═══════════════════════════════════════════════════════


def _make_ta_result(ticker: str, confidence: float = 70.0) -> StockAnalysisResult:
    return StockAnalysisResult(
        ticker=ticker,
        date="2026-08-11",
        signal="BUY",
        confidence=confidence,
    )


def _make_kronos_result(
    ticker: str, direction: str = "UP", change: float = 2.0,
) -> KronosForecastResult:
    return KronosForecastResult(
        ticker=ticker,
        eval_date="2026-08-11",
        horizon=30,
        predicted_close_mean=100.0,
        expected_change_pct=change,
        direction=direction,
    )


def _make_merged_pool(n=50):
    """生成 n 条合并结果用于打分基准测试。"""
    pool = []
    for i in range(n):
        pool.append(
            {
                "ticker": f"sh.{600000 + i}",
                "ta_confidence": 50.0 + (i % 50),
                "kronos_change_pct": round((i % 20) - 10, 2),
                "kronos_direction": "UP" if i % 2 == 0 else "DOWN",
                "risk_score_total": round(10.0 + (i % 80), 1),
                "kronos_prediction_uncertainty": {"confidence_score": 60.0 + (i % 30)},
                "rank": i + 1,
                "_pool_size": n,
            },
        )
    return pool


# ═══════════════════════════════════════════════════════
# merge_results 性能
# ═══════════════════════════════════════════════════════


class TestMergeResultsBenchmark:
    def test_merge_linear_50_stocks(self) -> None:
        from trade_krono_cli.constraints_config import ConstraintConfig
        from trade_krono_cli.pipeline.merge import merge_results

        ta_results = [_make_ta_result(f"sh.{600000 + i}") for i in range(50)]
        kronos_results = [_make_kronos_result(f"sh.{600000 + i}") for i in range(50)]
        # 禁用 ST 过滤以避免 baostock 登录开销
        constraints = ConstraintConfig(enable_st_filter=False)

        avg_ms = benchmark(
            lambda: merge_results(ta_results, kronos_results, constraints_config=constraints),
            iterations=50,
        )
        assert avg_ms < 500

    def test_merge_linear_200_stocks(self) -> None:
        from trade_krono_cli.constraints_config import ConstraintConfig
        from trade_krono_cli.pipeline.merge import merge_results

        ta_results = [_make_ta_result(f"sh.{600000 + i}") for i in range(200)]
        kronos_results = [_make_kronos_result(f"sh.{600000 + i}") for i in range(200)]
        constraints = ConstraintConfig(enable_st_filter=False)

        avg_ms = benchmark(
            lambda: merge_results(ta_results, kronos_results, constraints_config=constraints),
            iterations=20,
        )
        assert avg_ms < 2000

    def test_merge_multiplicative_50_stocks(self) -> None:
        from trade_krono_cli.configs.schema import ScoringStrategyConfig
        from trade_krono_cli.constraints_config import ConstraintConfig
        from trade_krono_cli.pipeline.merge import merge_results

        ta_results = [_make_ta_result(f"sh.{600000 + i}") for i in range(50)]
        kronos_results = [_make_kronos_result(f"sh.{600000 + i}") for i in range(50)]
        config = ScoringStrategyConfig(strategy="multiplicative")
        constraints = ConstraintConfig(enable_st_filter=False)

        avg_ms = benchmark(
            lambda: merge_results(ta_results, kronos_results, scoring_strategy=config, constraints_config=constraints),
            iterations=50,
        )
        assert avg_ms < 500


# ═══════════════════════════════════════════════════════
# Scorer strategies 性能对比
# ═══════════════════════════════════════════════════════


class TestScorerBenchmark:
    def test_linear_scorer_1000_items(self) -> None:
        from trade_krono_cli.scoring import LinearScorer

        pool = _make_merged_pool(1000)
        scorer = LinearScorer()

        avg_ms = benchmark(
            lambda: [scorer.score(m) for m in pool],
            iterations=20,
        )
        per_item_ms = avg_ms / 1000
        assert per_item_ms < 1.0

    def test_multiplicative_scorer_1000_items(self) -> None:
        from trade_krono_cli.scoring import MultiplicativeScorer

        pool = _make_merged_pool(1000)
        scorer = MultiplicativeScorer()

        avg_ms = benchmark(
            lambda: [scorer.score(m) for m in pool],
            iterations=20,
        )
        per_item_ms = avg_ms / 1000
        assert per_item_ms < 1.0

    def test_rank_based_scorer_1000_items(self) -> None:
        from trade_krono_cli.scoring import RankBasedScorer

        pool = _make_merged_pool(1000)
        scorer = RankBasedScorer()

        avg_ms = benchmark(
            lambda: [scorer.score(m) for m in pool],
            iterations=20,
        )
        per_item_ms = avg_ms / 1000
        assert per_item_ms < 1.0

    def test_scorer_strategy_switch_overhead(self) -> None:
        from trade_krono_cli.scoring import get_scorer_registry

        reg = get_scorer_registry()

        avg_ms = benchmark(
            lambda: [reg.get("linear"), reg.get("multiplicative"), reg.get("rank_based")],
            iterations=1000,
        )
        assert avg_ms < 100


# ═══════════════════════════════════════════════════════
# Risk boost strategies 性能
# ═══════════════════════════════════════════════════════


class TestRiskBoostBenchmark:
    def test_fixed_boost_1000_items(self) -> None:
        from trade_krono_cli.scoring import FixedBoostBooster

        pool = _make_merged_pool(1000)
        booster = FixedBoostBooster()

        avg_ms = benchmark(
            lambda: [
                booster.boost(
                    base_risk=m["risk_score_total"],
                    flags=["ST"] if m["risk_score_total"] > 60 else [],
                )
                for m in pool
            ],
            iterations=20,
        )
        per_item_ms = avg_ms / 1000
        assert per_item_ms < 1.0

    def test_scaled_boost_1000_items(self) -> None:
        from trade_krono_cli.scoring import ScaledBoostBooster

        pool = _make_merged_pool(1000)
        booster = ScaledBoostBooster()

        avg_ms = benchmark(
            lambda: [
                booster.boost(
                    base_risk=m["risk_score_total"], flags=["ST"], params={"multiplier": 1.5},
                )
                for m in pool
            ],
            iterations=20,
        )
        per_item_ms = avg_ms / 1000
        assert per_item_ms < 1.0

    def test_diminishing_boost_1000_items(self) -> None:
        from trade_krono_cli.scoring import DiminishingBoostBooster

        pool = _make_merged_pool(1000)
        booster = DiminishingBoostBooster()

        avg_ms = benchmark(
            lambda: [
                booster.boost(
                    base_risk=m["risk_score_total"],
                    flags=["ST", "DELISTED"] if m["risk_score_total"] > 70 else ["ST"],
                )
                for m in pool
            ],
            iterations=20,
        )
        per_item_ms = avg_ms / 1000
        assert per_item_ms < 1.0


# ═══════════════════════════════════════════════════════
# PipelineConfig operations
# ═══════════════════════════════════════════════════════


class TestPipelineConfigBenchmark:
    def test_config_default_creation(self) -> None:
        from trade_krono_cli.pipeline_config import PipelineConfig

        avg_ms = benchmark(PipelineConfig.default, iterations=500)
        assert avg_ms < 50

    def test_config_override(self) -> None:
        from trade_krono_cli.pipeline_config import PipelineConfig

        cfg = PipelineConfig.default()
        override = {
            "scoring_strategy": {"strategy": "multiplicative"},
            "risk_boost_strategy": {"strategy": "scaled_boost", "multiplier": 2.0},
            "min_confidence": 60.0,
        }

        avg_ms = benchmark(lambda: cfg.override(**override), iterations=500)
        assert avg_ms < 50

    def test_config_to_dict(self) -> None:
        from trade_krono_cli.pipeline_config import PipelineConfig

        cfg = PipelineConfig.default()

        avg_ms = benchmark(cfg.to_dict, iterations=500)
        assert avg_ms < 50


# ═══════════════════════════════════════════════════════
# Cache operations
# ═══════════════════════════════════════════════════════


class TestCacheBenchmark:
    def test_cache_ta_write_read(self, tmp_path) -> None:
        from trade_krono_cli.cache import Cache

        cache = Cache(db_path=tmp_path / "bench.db")
        key = "sh.600519"
        value = {"composite_score": 85.0, "ta_signal": "BUY"}

        # warmup
        cache.set_ta(key, "2026-08-11", value)
        cache.get_ta(key, "2026-08-11")

        def op():
            cache.set_ta(key, "2026-08-11", value)
            return cache.get_ta(key, "2026-08-11")

        avg_ms = benchmark(op, iterations=200)
        assert avg_ms < 50

    def test_cache_kronos_write_read(self, tmp_path) -> None:
        from trade_krono_cli.cache import Cache

        cache = Cache(db_path=tmp_path / "bench_k.db")
        key = "sh.600519"
        value = {"direction": "UP", "change_pct": 2.5}

        # warmup
        cache.set_kronos(key, "2026-08-11", 30, value)
        cache.get_kronos(key, "2026-08-11", 30)

        def op():
            cache.set_kronos(key, "2026-08-11", 30, value)
            return cache.get_kronos(key, "2026-08-11", 30)

        avg_ms = benchmark(op, iterations=200)
        assert avg_ms < 50


# ═══════════════════════════════════════════════════════
# Research database operations
# ═══════════════════════════════════════════════════════


class TestResearchDbBenchmark:
    def test_insert_and_query(self, tmp_path) -> None:
        from trade_krono_cli.research_db import ResearchDatabase

        db = ResearchDatabase(db_path=tmp_path / "bench.db")

        def op():
            db.insert_strategy_run(
                run_at=time.time(),
                strategy="linear",
                params={},
                tickers=["sh.600519"],
                results=[{"composite_score": 80.0}],
            )
            return db.query_strategy_history(limit=1)

        avg_ms = benchmark(op, iterations=50)
        assert avg_ms < 200


# ═══════════════════════════════════════════════════════
# CLI _load_tickers performance
# ═══════════════════════════════════════════════════════


class TestCliBenchmarks:
    def test_load_tickers_large_string(self) -> None:
        from trade_krono_cli.cli_commands.core import _load_tickers

        tickers_str = ",".join(f"{600000 + i}" for i in range(500))

        avg_ms = benchmark(
            lambda: _load_tickers(tickers_str, None),
            iterations=200,
        )
        per_item_ms = avg_ms / 500
        assert per_item_ms < 0.01

    def test_sanitize_path_resolution(self, tmp_path) -> None:
        from trade_krono_cli.cli_commands.core import _sanitize_path

        p = tmp_path / "outputs" / "result.json"
        p.parent.mkdir(parents=True, exist_ok=True)

        avg_ms = benchmark(
            lambda: _sanitize_path(str(p), "Test", tmp_path),
            iterations=500,
        )
        assert avg_ms < 10
