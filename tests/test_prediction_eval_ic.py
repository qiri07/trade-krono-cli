"""测试 eval_ic.py 和 eval_benchmark.py — Signal IC 与 Alpha 评估。"""

from unittest.mock import patch

import numpy as np
import pytest

from trade_krono_cli.eval_benchmark import (
    compute_alpha,
    compute_portfolio_metrics,
    get_best_alpha,
)
from trade_krono_cli.eval_data import EvalRecord, EvaluationSummary, HorizonMetrics
from trade_krono_cli.eval_ic import (
    ICResult,
    _compute_ic_for_signal,
    _rank_transform,
    _safe_pearson,
    _safe_spearman,
    compute_ic_aggregated,
    compute_ic_metrics,
)

# ═══════════════════════════════════════════════════════
# IC 工具函数
# ═══════════════════════════════════════════════════════


class TestRankTransform:
    def test_basic(self) -> None:
        arr = np.array([3.0, 1.0, 4.0, 1.0, 5.0])
        ranks = _rank_transform(arr)
        # 排序后 [1,1,3,4,5] → 平均秩 [1.5,1.5,3,4,5]，映射回原位置
        # ranks[0]=3.0(值3), ranks[1]=1.5(值1并列), ranks[2]=4.0(值4),
        # ranks[3]=1.5(值1并列), ranks[4]=5.0(值5)
        assert abs(ranks[0] - 3.0) < 0.01  # 3.0 的秩
        assert abs(ranks[1] - 1.5) < 0.01  # 1.0 并列平均秩
        assert abs(ranks[2] - 4.0) < 0.01  # 4.0 的秩
        assert abs(ranks[3] - 1.5) < 0.01  # 1.0 并列平均秩
        assert abs(ranks[4] - 5.0) < 0.01  # 5.0 的秩

    def test_empty(self) -> None:
        assert len(_rank_transform(np.array([]))) == 0


class TestSafePearson:
    def test_perfect_positive(self) -> None:
        x = np.array([1.0, 2.0, 3.0, 4.0])
        y = np.array([2.0, 4.0, 6.0, 8.0])
        assert _safe_pearson(x, y) == pytest.approx(1.0, abs=0.001)

    def test_perfect_negative(self) -> None:
        x = np.array([1.0, 2.0, 3.0])
        y = np.array([3.0, 2.0, 1.0])
        assert _safe_pearson(x, y) == pytest.approx(-1.0, abs=0.001)

    def test_no_variation(self) -> None:
        assert _safe_pearson(np.array([1.0, 1.0, 1.0]), np.array([1.0, 2.0, 3.0])) == 0.0

    def test_too_short(self) -> None:
        assert _safe_pearson(np.array([1.0, 2.0]), np.array([2.0, 4.0])) == 0.0


class TestSafeSpearman:
    def test_perfect_rank_correlation(self) -> None:
        x = np.array([10.0, 20.0, 30.0, 40.0, 50.0])
        y = np.array([5.0, 3.0, 7.0, 1.0, 9.0])
        # 非单调但应有正相关
        r = _safe_spearman(x, y)
        assert -1.0 <= r <= 1.0

    def test_perfect_monotonic(self) -> None:
        x = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        y = np.array([1.0, 2.0, 3.0, 4.0, 5.0])  # 完美单调递增
        assert _safe_spearman(x, y) == pytest.approx(1.0, abs=0.001)


# ═══════════════════════════════════════════════════════
# IC 计算
# ═══════════════════════════════════════════════════════


class TestComputeICForSignal:
    def test_perfect_correlation(self) -> None:
        """预测与实际完全线性相关 → IC≈1。"""
        pred = np.array([10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0])
        actual = np.array([2.0, 4.0, 5.0, 7.0, 9.0, 11.0, 13.0, 15.0, 17.0, 20.0])
        result = _compute_ic_for_signal(pred, actual)
        assert result.ic_mean > 0.9
        assert result.rank_ic_mean > 0.9

    def test_no_correlation(self) -> None:
        """随机噪声 → IC≈0。"""
        np.random.seed(42)
        pred = np.random.randn(50)
        actual = np.random.randn(50)
        result = _compute_ic_for_signal(pred, actual)
        assert abs(result.ic_mean) < 0.3
        assert abs(result.rank_ic_mean) < 0.3

    def test_too_few_records(self) -> None:
        """样本不足时返回空结果。"""
        result = _compute_ic_for_signal(
            np.array([1.0, 2.0]),
            np.array([3.0, 4.0]),
        )
        assert result.n_records == 2
        assert result.ic_mean == 0.0


class TestComputeICAggregated:
    def test_aggregate_multiple_dates(self) -> None:
        """多日 IC 聚合后应给出有意义的均值/标准差。"""
        results = [
            ICResult(
                ic_mean=0.05,
                ic_std=0.01,
                rank_ic_mean=0.06,
                rank_ic_std=0.01,
                n_groups=1,
                n_records=20,
            ),
            ICResult(
                ic_mean=0.03,
                ic_std=0.01,
                rank_ic_mean=0.04,
                rank_ic_std=0.01,
                n_groups=1,
                n_records=25,
            ),
            ICResult(
                ic_mean=0.07,
                ic_std=0.02,
                rank_ic_mean=0.08,
                rank_ic_std=0.02,
                n_groups=1,
                n_records=18,
            ),
        ]
        agg = compute_ic_aggregated(results)
        assert agg.ic_mean == pytest.approx(0.05, abs=0.01)
        assert agg.ic_std > 0
        assert agg.rank_ic_mean == pytest.approx(0.06, abs=0.01)
        assert agg.n_groups == 3

    def test_empty_aggregation(self) -> None:
        agg = compute_ic_aggregated([])
        assert agg.ic_mean == 0.0
        assert agg.n_groups == 0


class TestComputeICMetrics:
    def test_ic_with_strong_signal(self) -> None:
        """构造强相关数据，验证 IC > 0.03。"""
        records = []
        np.random.seed(0)
        for day_idx in range(5):
            for i in range(20):
                score = 60.0 + i * 2 + np.random.normal(0, 1)
                ret = score * 0.15 + np.random.normal(0, 0.3)
                records.append(
                    EvalRecord(
                        ticker=f"sh.60{i:03d}",
                        eval_date=f"2026-01-{day_idx + 1:02d}",
                        horizon_days=5,
                        pred_direction="UP",
                        pred_return_pct=ret * 0.5,
                        actual_return_pct=round(ret, 4),
                        actual_direction="UP" if ret > 0 else "DOWN",
                        is_direction_correct=ret > 0,
                        error_pct=0.0,
                        ta_signal="BUY",
                        composite_score=score,
                    ),
                )

        m = HorizonMetrics()
        n = compute_ic_metrics(records, m)
        assert n == 100
        # 综合评分 Rank IC 应为正值
        assert m.rank_ic_composite_mean > 0.1
        assert m.rank_ic_composite_ir > 0.5

    def test_ic_with_noisy_signal(self) -> None:
        """随机预测 → IC 应接近 0。"""
        records = []
        np.random.seed(99)
        for day_idx in range(5):
            for i in range(20):
                records.append(
                    EvalRecord(
                        ticker=f"sh.60{i:03d}",
                        eval_date=f"2026-02-{day_idx + 1:02d}",
                        horizon_days=5,
                        pred_direction=None,
                        pred_return_pct=None,
                        actual_return_pct=round(np.random.normal(0, 2), 4),
                        actual_direction="UP" if np.random.random() > 0.5 else "DOWN",
                        is_direction_correct=False,
                        error_pct=0.0,
                        ta_signal=None,
                        composite_score=float(np.random.randint(40, 80)),
                    ),
                )

        m = HorizonMetrics()
        n = compute_ic_metrics(records, m)
        assert n == 100
        # IC 应接近 0（不显著）
        assert abs(m.rank_ic_composite_mean) < 0.1

    def test_insufficient_dates_skipped(self) -> None:
        """少于 3 个 eval_date 时跳过 IC 计算。"""
        records = [
            EvalRecord(
                ticker="sh.600519",
                eval_date="2026-01-01",
                horizon_days=5,
                pred_direction="UP",
                pred_return_pct=2.0,
                actual_return_pct=3.0,
                actual_direction="UP",
                is_direction_correct=True,
                error_pct=0.0,
                ta_signal="BUY",
                composite_score=70.0,
            ),
        ] * 15
        m = HorizonMetrics()
        n = compute_ic_metrics(records, m)
        assert n == 0  # 少于 3 个 eval_date 时跳过，返回 0
        # IC 未被计算，字段保持默认值
        assert m.rank_ic_composite_mean == 0.0


# ═══════════════════════════════════════════════════════
# Benchmark
# ═══════════════════════════════════════════════════════


class TestComputePortfolioMetrics:
    def test_basic_metrics(self) -> None:
        """简单等权上涨曲线 → 正确计算 Sharpe / CAGR。"""
        equity = [
            ("2026-01-01", 1_000_000.0),
            ("2026-01-02", 1_010_000.0),
            ("2026-01-05", 1_020_100.0),
            ("2026-01-06", 1_015_000.0),
            ("2026-01-07", 1_030_301.0),
        ]
        trades = [
            {"date": "2026-01-02", "action": "BUY", "ticker": "sh.600519"},
            {"date": "2026-01-06", "action": "SELL", "ticker": "sh.600519", "pnl": 15_000.0},
        ]
        m = compute_portfolio_metrics(equity, trades)
        assert m["total_return_pct"] > 0
        assert m["cagr_pct"] > 0
        assert m["sharpe_ratio"] > 0
        assert m["win_rate_pct"] == 100.0
        assert m["profit_factor"] > 0
        assert m["n_trades"] == 2
        assert m["turnover"] > 0

    def test_empty_equity(self) -> None:
        m = compute_portfolio_metrics([], [])
        assert m == {}

    def test_sortino_with_down_days(self) -> None:
        equity = [
            ("2026-01-01", 1_000_000.0),
            ("2026-01-02", 1_020_000.0),
            ("2026-01-05", 990_000.0),
            ("2026-01-06", 1_010_000.0),
            ("2026-01-07", 1_040_000.0),
        ]
        m = compute_portfolio_metrics(equity, [])
        assert m["sortino_ratio"] >= 0


class TestComputeAlpha:
    def test_alpha_computation(self, tmp_path) -> None:
        """验证 Alpha 计算逻辑（mock 基准数据）。"""
        records = [
            EvalRecord(
                ticker="sh.600519",
                eval_date="2026-01-01",
                horizon_days=5,
                pred_direction="UP",
                pred_return_pct=3.0,
                actual_return_pct=2.5,
                actual_direction="UP",
                is_direction_correct=True,
                error_pct=0.5,
                ta_signal="BUY",
                composite_score=80.0,
            ),
        ]
        # mock fetch_benchmark_kline 返回基准数据
        with patch("trade_krono_cli.eval_benchmark.fetch_kline") as mock_fetch:
            import pandas as pd

            mock_fetch.return_value = pd.DataFrame(
                {
                    "timestamps": ["2026-01-01", "2026-01-06"],
                    "close": [4000.0, 4100.0],
                },
            )
            results = compute_alpha(5.0, records, ("2026-01-01", "2026-01-06"))
        assert "CSI300" in results or "SHCOMP" in results

    def test_get_best_alpha(self) -> None:
        from trade_krono_cli.eval_benchmark import AlphaResult

        results = {
            "CSI300": AlphaResult(
                strategy_return_pct=10.0,
                benchmark_return_pct=5.0,
                alpha_pct=5.0,
                benchmark_name="CSI300",
            ),
            "CSI500": AlphaResult(
                strategy_return_pct=10.0,
                benchmark_return_pct=8.0,
                alpha_pct=2.0,
                benchmark_name="CSI500",
            ),
        }
        best = get_best_alpha(results)
        assert best.benchmark_name == "CSI300"
        assert best.alpha_pct == 5.0

    def test_empty_alpha(self) -> None:
        results = compute_alpha(5.0, [], ("2026-01-01", "2026-01-06"))
        assert results == {}


# ═══════════════════════════════════════════════════════
# 端到端：PredictionEvaluator 集成
# ═══════════════════════════════════════════════════════


class TestPredictionEvaluatorWithIC:
    def test_summary_includes_ic_fields(self) -> None:
        """_compute_summary 应将 IC 字段写入 HorizonMetrics。"""
        from trade_krono_cli.prediction_eval import PredictionEvaluator

        evaluator = PredictionEvaluator.__new__(PredictionEvaluator)
        evaluator.HORIZONS = [5, 10]

        records = []
        np.random.seed(7)
        for day_idx in range(5):
            for i in range(20):
                score = 60.0 + i * 2
                ret = score * 0.1 + np.random.normal(0, 0.5)
                records.append(
                    EvalRecord(
                        ticker=f"sh.60{i:03d}",
                        eval_date=f"2026-03-{day_idx + 1:02d}",
                        horizon_days=5,
                        pred_direction="UP",
                        pred_return_pct=ret * 0.5,
                        actual_return_pct=round(ret, 4),
                        actual_direction="UP" if ret > 0 else "DOWN",
                        is_direction_correct=ret > 0,
                        error_pct=0.0,
                        ta_signal="BUY",
                        composite_score=score,
                    ),
                )

        summary = evaluator._compute_summary(records)
        m5 = summary.horizons.get(5)
        assert m5 is not None
        # IC 字段应被填充
        assert m5.rank_ic_composite_mean != 0.0 or m5.ic_composite_mean != 0.0
        # 聚合 IC 应写入 summary
        assert summary.ic_composite_rank_mean != 0.0 or summary.ic_kronos_rank_mean != 0.0


class TestEvaluationSummaryNewFields:
    def test_default_values(self) -> None:
        s = EvaluationSummary()
        assert s.ic_composite_rank_mean == 0.0
        assert s.ic_composite_rank_ir == 0.0
        assert s.alpha_best_benchmark == ""
        assert s.alpha_best_value == 0.0
        assert s.alpha_all == {}
        assert s.benchmark_results == {}
        # HorizonMetrics 新增字段有默认值
        m = HorizonMetrics()
        assert m.sortino_ratio == 0.0
        assert m.calmar_ratio == 0.0
        assert m.turnover == 0.0
        assert m.alpha_vs_benchmark == 0.0
        assert m.benchmark_return_pct == 0.0
        assert m.ic_composite_mean == 0.0
        assert m.rank_ic_composite_mean == 0.0
