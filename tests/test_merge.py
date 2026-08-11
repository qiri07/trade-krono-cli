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
    # Uncertainty: 70 * 0.1 = 7
    # Total: 32 + 15.96 + 1 + 7 = 55.96
    assert score == pytest.approx(55.96, abs=0.1)


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
    # Uncertainty: 60 * 0.1 = 6
    # Total: 32 + 14.55 - 1 + 6 = 51.55
    assert score == pytest.approx(51.55, abs=0.1)


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
    # Uncertainty: 50 * 0.1 = 5
    # Total: 24 + 15 + 0 + 5 = 44
    assert score == pytest.approx(44.0, abs=0.1)


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
