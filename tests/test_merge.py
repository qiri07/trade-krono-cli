"""测试合并和打分逻辑。"""
import pytest
from trade_krono_cli.merge import default_scorer, merge_results, filter_pool
from trade_krono_cli.ta_runner import StockAnalysisResult
from trade_krono_cli.kronos_runner import KronosForecastResult


def test_default_scorer_buy_up():
    merged = {
        "ta_confidence": 80.0,
        "kronos_change_pct": 3.2,
        "kronos_direction": "UP",
    }
    score = default_scorer(merged)
    # TA: 80 * 0.4 = 32
    # Kronos: max(0, min(100, 3.2 + 50)) * 0.4 = 53.2 * 0.4 = 21.28
    # Direction: +20 * 0.2 = 4
    assert score == pytest.approx(57.28, abs=0.1)


def test_default_scorer_sell_down():
    merged = {
        "ta_confidence": 80.0,
        "kronos_change_pct": -1.5,
        "kronos_direction": "DOWN",
    }
    score = default_scorer(merged)
    # TA: 80 * 0.4 = 32
    # Kronos: max(0, min(100, -1.5 + 50)) * 0.4 = 48.5 * 0.4 = 19.4
    # Direction: -20 * 0.2 = -4
    assert score == pytest.approx(47.4, abs=0.1)


def test_default_scorer_flat():
    merged = {
        "ta_confidence": 60.0,
        "kronos_change_pct": 0.0,
        "kronos_direction": "FLAT",
    }
    score = default_scorer(merged)
    # TA: 60 * 0.4 = 24
    # Kronos: 50 * 0.4 = 20
    # Direction: 0
    assert score == pytest.approx(44.0, abs=0.1)


def test_merge_results():
    ta_results = [
        StockAnalysisResult(ticker="sh.600519", date="2026-08-11", signal="BUY", confidence=80.0),
        StockAnalysisResult(ticker="sz.000858", date="2026-08-11", signal="SELL", confidence=70.0),
    ]
    kronos_results = [
        KronosForecastResult(
            ticker="sh.600519", eval_date="2026-08-11", horizon=30,
            direction="UP", expected_change_pct=3.2, last_close=1780.5,
        ),
        KronosForecastResult(
            ticker="sz.000858", eval_date="2026-08-11", horizon=30,
            direction="DOWN", expected_change_pct=-1.5, last_close=25.3,
        ),
    ]

    merged = merge_results(ta_results, kronos_results)
    assert len(merged) == 2
    assert merged[0]["rank"] == 1
    assert merged[1]["rank"] == 2
    # 600519 should rank higher (BUY + UP)
    assert merged[0]["ticker"] == "sh.600519"


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
        StockAnalysisResult(ticker="sz.300001", date="2026-08-11", signal="BUY", confidence=40.0),  # 低于阈值
    ]

    pool = filter_pool(ta_results, min_confidence=55.0, allowed_signals=("BUY", "HOLD"))
    assert len(pool) == 2
    tickers = {p["ticker"] for p in pool}
    assert "sh.600519" in tickers
    assert "sh.600036" in tickers
    assert "sz.000858" not in tickers  # SELL
    assert "sz.300001" not in tickers  # confidence < 55


def test_empty_merge():
    merged = merge_results([], [])
    assert merged == []
