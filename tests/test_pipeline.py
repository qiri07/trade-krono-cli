"""端到端集成测试（mock TA 和 Kronos）。"""

from pathlib import Path
from unittest.mock import MagicMock


def test_pipeline_run_parallel():
    """测试并行流水线（mock TA 和 Kronos）。"""
    from trade_krono_cli.kronos_runner import KronosForecastResult, PredictionUncertainty
    from trade_krono_cli.pipeline import QuantPipeline
    from trade_krono_cli.ta_runner import StockAnalysisResult

    mock_ta = MagicMock()
    mock_ta.analyze_batch.return_value = [
        StockAnalysisResult(ticker="sh.600519", date="2026-08-11", signal="BUY", confidence=80.0),
        StockAnalysisResult(ticker="sz.000858", date="2026-08-11", signal="HOLD", confidence=60.0),
    ]

    pu = PredictionUncertainty(
        expected_return=3.2,
        direction="UP",
        direction_score=0.8,
        confidence_score=75.0,
        sample_count_used=1,
    )
    mock_kr = MagicMock()
    mock_kr.predict_batch.return_value = [
        KronosForecastResult(
            ticker="sh.600519",
            eval_date="2026-08-11",
            horizon=30,
            direction="UP",
            expected_change_pct=3.2,
            last_close=1780.5,
            prediction_uncertainty=pu,
        ),
        KronosForecastResult(
            ticker="sz.000858",
            eval_date="2026-08-11",
            horizon=30,
            direction="DOWN",
            expected_change_pct=-1.5,
            last_close=25.3,
            prediction_uncertainty=PredictionUncertainty(
                expected_return=-1.5,
                direction="DOWN",
                direction_score=0.6,
                confidence_score=60.0,
                sample_count_used=1,
            ),
        ),
    ]

    pipeline = QuantPipeline(
        ta_runner=mock_ta,
        kronos_runner=mock_kr,
        skip_kronos=False,
    )

    merged = pipeline.run_parallel(
        tickers=["600519", "000858"],
        date="2026-08-11",
        output_json="/tmp/test_merged.json",
    )

    assert len(merged) == 2
    assert merged[0]["rank"] == 1
    assert merged[0]["ticker"] == "sh.600519"
    assert merged[0]["ta_signal"] == "BUY"
    assert merged[0]["kronos_direction"] == "UP"
    assert merged[0]["kronos_prediction_uncertainty"]["confidence_score"] == 75.0

    assert Path("/tmp/test_merged.json").exists()


def test_pipeline_ta_only():
    """测试仅 TA 模式。"""
    from trade_krono_cli.pipeline import QuantPipeline
    from trade_krono_cli.ta_runner import StockAnalysisResult

    mock_ta = MagicMock()
    mock_ta.analyze_batch.return_value = [
        StockAnalysisResult(ticker="sh.600519", date="2026-08-11", signal="BUY", confidence=85.0),
    ]

    pipeline = QuantPipeline(ta_runner=mock_ta, skip_kronos=True)

    merged = pipeline.run_ta_only(
        tickers=["600519"],
        date="2026-08-11",
        output="/tmp/test_ta.json",
    )

    assert len(merged) == 1
    assert merged[0].signal == "BUY"


def test_pipeline_kronos_only():
    """测试仅 Kronos 模式。"""
    from trade_krono_cli.kronos_runner import KronosForecastResult
    from trade_krono_cli.pipeline import QuantPipeline

    mock_kr = MagicMock()
    mock_kr.predict_batch.return_value = [
        KronosForecastResult(
            ticker="sh.600519",
            eval_date="2026-08-11",
            horizon=30,
            direction="UP",
            expected_change_pct=2.5,
        ),
    ]

    pipeline = QuantPipeline(kronos_runner=mock_kr, skip_kronos=False)

    results = pipeline.run_kronos_only(
        tickers=["600519"],
        date="2026-08-11",
        output="/tmp/test_kronos.json",
    )

    assert len(results) == 1
    assert results[0].direction == "UP"


def test_pipeline_with_errors():
    """测试容错性：单只股票失败不影响整体。"""
    from trade_krono_cli.kronos_runner import KronosForecastResult
    from trade_krono_cli.pipeline import QuantPipeline
    from trade_krono_cli.ta_runner import StockAnalysisResult

    mock_ta = MagicMock()
    mock_ta.analyze_batch.return_value = [
        StockAnalysisResult(ticker="sh.600519", date="2026-08-11", signal="BUY", confidence=80.0),
        StockAnalysisResult(ticker="sz.000858", date="2026-08-11", error="Network error"),
    ]

    mock_kr = MagicMock()
    mock_kr.predict_batch.return_value = [
        KronosForecastResult(
            ticker="sh.600519",
            eval_date="2026-08-11",
            horizon=30,
            direction="UP",
            expected_change_pct=3.2,
        ),
        KronosForecastResult(
            ticker="sz.000858",
            eval_date="2026-08-11",
            horizon=30,
            error="Model error",
        ),
    ]

    pipeline = QuantPipeline(ta_runner=mock_ta, kronos_runner=mock_kr)

    merged = pipeline.run_parallel(
        tickers=["600519", "000858"],
        date="2026-08-11",
    )

    assert len(merged) == 1
    assert merged[0]["ticker"] == "sh.600519"
    assert merged[0]["ta_signal"] == "BUY"
    assert merged[0]["ta_confidence"] == 80.0
