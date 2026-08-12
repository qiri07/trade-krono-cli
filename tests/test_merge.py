"""测试合并和打分逻辑。"""
import pytest
from trade_krono_cli.merge import default_scorer, merge_results, filter_pool
from trade_krono_cli.ta_runner import StockAnalysisResult
from trade_krono_cli.kronos_runner import KronosForecastResult, PredictionUncertainty


def test_default_scorer_buy_up():
    merged = {
        "ta_confidence": 80.0,
        "kronos_change_pct": 3.2,
        "kronos_direction": "UP",
        "kronos_prediction_uncertainty": {"confidence_score": 70.0},
    }
    score = default_scorer(merged)
    # TA: 80 * 0.4 = 32
    # Kronos: max(0, min(100, 3.2 + 50)) * 0.3 = 53.2 * 0.3 = 15.96
    # Direction: +10 * 0.1 = +1
    # Uncertainty base: 70 * 0.1 = 7
    # Uncertainty bonus (cs>=70 → +3): +3
    # Total: 32 + 15.96 + 1 + 7 + 3 = 58.96
    assert score == pytest.approx(58.96, abs=0.1)


def test_default_scorer_sell_down():
    merged = {
        "ta_confidence": 80.0,
        "kronos_change_pct": -1.5,
        "kronos_direction": "DOWN",
        "kronos_prediction_uncertainty": {"confidence_score": 60.0},
    }
    score = default_scorer(merged)
    # TA: 80 * 0.4 = 32
    # Kronos: max(0, min(100, -1.5 + 50)) * 0.3 = 48.5 * 0.3 = 14.55
    # Direction: -10 * 0.1 = -1
    # Uncertainty base: 60 * 0.1 = 6
    # Uncertainty bonus (50<=cs<70 → +1): +1
    # Total: 32 + 14.55 - 1 + 6 + 1 = 52.55
    assert score == pytest.approx(52.55, abs=0.1)


def test_default_scorer_flat():
    merged = {
        "ta_confidence": 60.0,
        "kronos_change_pct": 0.0,
        "kronos_direction": "FLAT",
        "kronos_prediction_uncertainty": {"confidence_score": 50.0},
    }
    score = default_scorer(merged)
    # TA: 60 * 0.4 = 24
    # Kronos: 50 * 0.3 = 15
    # Direction: 0
    # Uncertainty base: 50 * 0.1 = 5
    # Uncertainty bonus (50<=cs<70 → +1): +1
    # Total: 24 + 15 + 0 + 5 + 1 = 45
    assert score == pytest.approx(45.0, abs=0.1)


def test_merge_results():
    ta_results = [
        StockAnalysisResult(ticker="sh.600519", date="2026-08-11", signal="BUY", confidence=80.0),
        StockAnalysisResult(ticker="sz.000858", date="2026-08-11", signal="SELL", confidence=70.0),
    ]
    pu = PredictionUncertainty(
        expected_return=3.2, direction="UP", direction_confidence=0.8,
        confidence_score=75.0, sample_count_used=1,
    )
    kronos_results = [
        KronosForecastResult(
            ticker="sh.600519", eval_date="2026-08-11", horizon=30,
            direction="UP", expected_change_pct=3.2, last_close=1780.5,
            prediction_uncertainty=pu,
        ),
        KronosForecastResult(
            ticker="sz.000858", eval_date="2026-08-11", horizon=30,
            direction="DOWN", expected_change_pct=-1.5, last_close=25.3,
            prediction_uncertainty=PredictionUncertainty(
                expected_return=-1.5, direction="DOWN", direction_confidence=0.6,
                confidence_score=60.0, sample_count_used=1,
            ),
        ),
    ]

    merged = merge_results(ta_results, kronos_results)
    assert len(merged) == 2
    assert merged[0]["rank"] == 1
    assert merged[1]["rank"] == 2
    assert merged[0]["ticker"] == "sh.600519"
    assert merged[0]["kronos_direction"] == "UP"
    assert merged[0]["kronos_prediction_uncertainty"]["confidence_score"] == 75.0


def test_merge_results_with_errors():
    ta_results = [
        StockAnalysisResult(ticker="sh.600519", date="2026-08-11", signal="BUY", confidence=80.0),
        StockAnalysisResult(ticker="sz.000858", date="2026-08-11", error="Test error"),
    ]
    kronos_results = [
        KronosForecastResult(
            ticker="sh.600519", eval_date="2026-08-11", horizon=30,
            direction="UP", expected_change_pct=3.2,
        ),
    ]

    merged = merge_results(ta_results, kronos_results)
    assert len(merged) == 2
    assert merged[1]["ticker"] == "sz.000858"
    assert merged[1]["ta_error"] == "Test error"


def test_filter_pool():
    ta_results = [
        StockAnalysisResult(ticker="sh.600519", date="2026-08-11", signal="BUY", confidence=80.0),
        StockAnalysisResult(ticker="sz.000858", date="2026-08-11", signal="SELL", confidence=70.0),
        StockAnalysisResult(ticker="sh.600036", date="2026-08-11", signal="HOLD", confidence=60.0),
        StockAnalysisResult(ticker="sz.300001", date="2026-08-11", signal="BUY", confidence=40.0),
    ]

    pool = filter_pool(ta_results, min_confidence=55.0, allowed_signals=("BUY", "HOLD"))
    assert len(pool) == 2
    tickers = {p["ticker"] for p in pool}
    assert "sh.600519" in tickers
    assert "sh.600036" in tickers
    assert "sz.000858" not in tickers
    assert "sz.300001" not in tickers


def test_empty_merge():
    merged = merge_results([], [])
    assert merged == []


# ── TA 决策提取逻辑测试 ─────────────────────────────────────────────────────

def test_merge_with_confidence():
    """merged 结果中 ta_confidence 不再为 None。"""
    from trade_krono_cli.ta_runner import StockAnalysisResult
    from trade_krono_cli.kronos_runner import KronosForecastResult

    ta = StockAnalysisResult(
        ticker="sh.600519", date="2026-08-11",
        signal="BUY", confidence=80.0,
    )
    kronos = KronosForecastResult(
        ticker="sh.600519", eval_date="2026-08-11", horizon=30,
        direction="UP", expected_change_pct=3.2,
    )

    merged = merge_results([ta], [kronos])
    assert len(merged) == 1
    assert merged[0]["ta_confidence"] == 80.0
    # 置信度 80 × 0.4 = 32 分，不应再是 0
    assert merged[0]["composite_score"] > 30


# ── Risk Engine 集成测试 ─────────────────────────────────────────────────────

def test_merge_with_risk_data():
    """有 K 线数据时应计算风险分并影响综合得分。"""
    import pandas as pd
    import numpy as np
    from trade_krono_cli.merge import merge_results

    ta = StockAnalysisResult(ticker="sh.600519", date="2026-08-11", signal="BUY", confidence=80.0)
    kronos = KronosForecastResult(ticker="sh.600519", eval_date="2026-08-11", horizon=30, direction="UP", expected_change_pct=3.2)

    np.random.seed(42)
    close_vals = 100 * (1 + np.random.randn(60) * 0.04)
    kline_df = pd.DataFrame({
        "open": close_vals * 0.99, "high": close_vals * 1.01,
        "low": close_vals * 0.98, "close": close_vals,
        "volume": pd.Series([1e7] * 60),
    })

    merged = merge_results([ta], [kronos], kline_data={"sh.600519": kline_df})
    assert len(merged) == 1
    assert merged[0]["risk_score_total"] is not None
    assert 0 <= merged[0]["risk_score_total"] <= 100
    assert merged[0]["risk_scores"] is not None
    for dim in ("volatility", "drawdown", "liquidity", "concentration", "market_regime"):
        assert dim in merged[0]["risk_scores"]


def test_risk_penalty_reduces_score():
    """高风险应降低综合得分。"""
    import pandas as pd
    import numpy as np
    from trade_krono_cli.merge import merge_results

    ta = StockAnalysisResult(ticker="sh.600519", date="2026-08-11", signal="BUY", confidence=80.0)
    kronos = KronosForecastResult(ticker="sh.600519", eval_date="2026-08-11", horizon=30, direction="UP", expected_change_pct=3.2)

    # 无风险数据
    merged_no_risk = merge_results([ta], [kronos])
    score_no_risk = merged_no_risk[0]["composite_score"]

    # 高波动数据（高风险）
    np.random.seed(99)
    close_high_vol = 100 * (1 + np.random.randn(60) * 0.06)
    kline_high_vol = pd.DataFrame({
        "open": close_high_vol * 0.99, "high": close_high_vol * 1.01,
        "low": close_high_vol * 0.98, "close": close_high_vol,
        "volume": pd.Series([5e6] * 60),
    })
    merged_with_risk = merge_results([ta], [kronos], kline_data={"sh.600519": kline_high_vol})
    score_with_risk = merged_with_risk[0]["composite_score"]

    # 无风险时 risk_score_total 为 None，不扣分
    assert merged_no_risk[0]["risk_score_total"] is None
    # 有高风险时分数应更低
    assert merged_with_risk[0]["risk_score_total"] > 0
    assert score_with_risk < score_no_risk


def test_merge_with_quote_data():
    """提供 quote_data 时应计算换手率。"""
    import pandas as pd
    from trade_krono_cli.merge import merge_results

    ta = StockAnalysisResult(ticker="sh.600519", date="2026-08-11", signal="BUY", confidence=70.0)
    kronos = KronosForecastResult(ticker="sh.600519", eval_date="2026-08-11", horizon=30, direction="UP", expected_change_pct=2.0)

    close_vals = [100 + i * 0.1 for i in range(60)]
    kline_df = pd.DataFrame({
        "open": [c * 0.99 for c in close_vals],
        "high": [c * 1.01 for c in close_vals],
        "low": [c * 0.98 for c in close_vals],
        "close": close_vals,
        "volume": [1e7] * 60,
    })

    merged = merge_results(
        [ta], [kronos],
        kline_data={"sh.600519": kline_df},
        quote_data={"sh.600519": {"market_cap": 200.0}},
    )
    assert merged[0]["risk_score_total"] is not None
    assert merged[0]["risk_scores"] is not None
