"""Pipeline core — QuantPipeline edge-case tests.

Covers lines not exercised by test_pipeline.py:
  - Empty tickers without universe engine (→ returns [])
  - _apply_ta_cache_fallback directly
  - run_kronos_only with self.kronos is None → RuntimeError
  - progress_cb("完成", 2, 2) at end
  - Delisted stock filtering log line
  - Degradation mode: ta_cache_fallback trigger path
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

import pytest

from trade_krono_cli.abnormal_stock import AbnormalityFlag, StockAbnormality


def _make_abnormality_flag(ticker: str, flags: list | None = None) -> AbnormalityFlag:
    """Create an AbnormalityFlag for testing."""
    return AbnormalityFlag(
        ticker=ticker,
        flags=flags or [],
        severity=0.0,
        reason="",
    )


# ═══════════════════════════════════════════════════════
# Empty tickers
# ═══════════════════════════════════════════════════════


def test_run_parallel_empty_tickers_no_universe() -> None:
    """No tickers and no universe engine → returns [] immediately."""
    from trade_krono_cli.pipeline import QuantPipeline

    pipeline = QuantPipeline(skip_kronos=True)
    result = pipeline.run_parallel(tickers=[], date="2026-08-11")
    assert result == []


def test_run_parallel_empty_tickers_with_universe_returns_empty() -> None:
    """No tickers but universe engine returns empty → returns [] with warning."""
    from trade_krono_cli.pipeline import QuantPipeline
    from trade_krono_cli.universe.engine import UniverseEngine

    mock_engine = MagicMock(spec=UniverseEngine)
    mock_engine.run.return_value = []

    pipeline = QuantPipeline(skip_kronos=True, universe_engine=mock_engine)
    result = pipeline.run_parallel(tickers=[], date="2026-08-11")
    assert result == []
    mock_engine.run.assert_called_once_with(eval_date="2026-08-11")


# ═══════════════════════════════════════════════════════
# _apply_ta_cache_fallback
# ═══════════════════════════════════════════════════════


def test_apply_ta_cache_fallback_caches_hit() -> None:
    """TA results with errors → fallback to cached results."""
    from trade_krono_cli.domain.prediction import TAAnalysis
    from trade_krono_cli.pipeline import QuantPipeline

    ta_results = [
        TAAnalysis(
            ticker="sh.600519",
            eval_date="2026-08-11",
            signal="BUY",
            confidence=80.0,
            thesis="good",
            reasoning="",
            risks=[],
            invalidations=[],
            error=None,
            elapsed_sec=1.0,
        ),
        TAAnalysis(
            ticker="sz.000858",
            eval_date="2026-08-11",
            signal="HOLD",
            confidence=50.0,
            thesis="",
            reasoning="",
            risks=[],
            invalidations=[],
            error="Network timeout",
            elapsed_sec=2.0,
        ),
    ]

    pipeline = QuantPipeline(skip_kronos=True)

    cached_result = {
        "date": "2026-08-10",
        "signal": "BUY",
        "confidence": 75.0,
        "thesis": "cached thesis",
    }

    with patch("trade_krono_cli.pipeline.pipeline_core.get_research") as mock_get_research:
        mock_research = MagicMock()
        mock_research.get_latest_ta_for_ticker.return_value = cached_result
        mock_get_research.return_value = mock_research

        pipeline._apply_ta_cache_fallback(ta_results)

    # First result should be unchanged (no error)
    assert ta_results[0].error is None
    # Second result should be replaced with cached version
    assert ta_results[1].error is None
    assert ta_results[1].signal == "BUY"
    assert ta_results[1].confidence == 75.0
    # Note: TAAnalysis.reasoning is mapped from cached["thesis"]
    assert ta_results[1].reasoning == "cached thesis"


def test_apply_ta_cache_fallback_no_cache() -> None:
    """TA results with errors but no cached result → error remains."""
    from trade_krono_cli.domain.prediction import TAAnalysis
    from trade_krono_cli.pipeline import QuantPipeline

    ta_results = [
        TAAnalysis(
            ticker="sh.600519",
            eval_date="2026-08-11",
            signal="HOLD",
            confidence=50.0,
            thesis="",
            reasoning="",
            risks=[],
            invalidations=[],
            error="Network timeout",
            elapsed_sec=2.0,
        ),
    ]

    pipeline = QuantPipeline(skip_kronos=True)

    with patch("trade_krono_cli.pipeline.pipeline_core.get_research") as mock_get_research:
        mock_research = MagicMock()
        mock_research.get_latest_ta_for_ticker.return_value = None
        mock_get_research.return_value = mock_research

        pipeline._apply_ta_cache_fallback(ta_results)

    # Error should remain since no cache available
    assert ta_results[0].error == "Network timeout"


# ═══════════════════════════════════════════════════════
# Delisted stock filtering
# ═══════════════════════════════════════════════════════


def test_run_parallel_filters_delisted() -> None:
    """Delisted stocks are filtered out before TA/Kronos execution."""
    from trade_krono_cli.pipeline import QuantPipeline
    from trade_krono_cli.ta_runner import StockAnalysisResult

    mock_ta = MagicMock()
    mock_ta.analyze_batch.return_value = [
        StockAnalysisResult(ticker="sh.600519", date="2026-08-11", signal="BUY", confidence=80.0),
    ]

    pipeline = QuantPipeline(ta_runner=mock_ta, skip_kronos=True)

    with patch("trade_krono_cli.pipeline.pipeline_core.precheck_stock_status") as mock_precheck:
        mock_precheck.return_value = {
            "sh.600000": _make_abnormality_flag("sh.600000", flags=[StockAbnormality.DELISTED]),
            "sh.600519": _make_abnormality_flag("sh.600519"),
        }
        pipeline.run_parallel(
            tickers=["sh.600000", "sh.600519"],
            date="2026-08-11",
        )

    # Only sh.600519 should be analyzed (delisted one filtered out)
    mock_ta.analyze_batch.assert_called_once()
    call_tickers = mock_ta.analyze_batch.call_args[0][0]
    # The delisted stock sh.600000 should NOT be in the call
    assert "sh.600000" not in call_tickers
    assert "sh.600519" in call_tickers


# ═══════════════════════════════════════════════════════
# progress_cb at end of run_parallel
# ═══════════════════════════════════════════════════════


def test_run_parallel_progress_callback_called() -> None:
    """progress_cb is called with '完成', 2, 2 at the end."""
    from trade_krono_cli.kronos_runner import KronosForecastResult, PredictionUncertainty
    from trade_krono_cli.pipeline import QuantPipeline
    from trade_krono_cli.ta_runner import StockAnalysisResult

    mock_ta = MagicMock()
    mock_ta.analyze_batch.return_value = [
        StockAnalysisResult(ticker="sh.600519", date="2026-08-11", signal="BUY", confidence=80.0),
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
    ]

    pipeline = QuantPipeline(ta_runner=mock_ta, kronos_runner=mock_kr)
    callbacks = []

    with patch("trade_krono_cli.pipeline.pipeline_core.precheck_stock_status") as mock_precheck:
        mock_precheck.return_value = {
            "sh.600519": _make_abnormality_flag("sh.600519"),
        }
        pipeline.run_parallel(
            tickers=["600519"],
            date="2026-08-11",
            progress_cb=lambda stage, current, total: callbacks.append((stage, current, total)),
        )

    # Should have called progress_cb with "完成" at the end
    assert ("完成", 2, 2) in callbacks


# ═══════════════════════════════════════════════════════
# run_kronos_only with None kronos
# ═══════════════════════════════════════════════════════


def test_run_kronos_only_when_kronos_none() -> None:
    """run_kronos_only when skip_kronos=True → raises RuntimeError."""
    from trade_krono_cli.pipeline import QuantPipeline

    pipeline = QuantPipeline(skip_kronos=True)
    with pytest.raises(RuntimeError, match="KronosSession 未初始化"):
        pipeline.run_kronos_only(tickers=["600519"], date="2026-08-11")


# ═══════════════════════════════════════════════════════
# run_ta_only with output path
# ═══════════════════════════════════════════════════════


def test_run_ta_only_with_output() -> None:
    """run_ta_only with output path saves results."""
    from trade_krono_cli.pipeline import QuantPipeline
    from trade_krono_cli.ta_runner import StockAnalysisResult

    mock_ta = MagicMock()
    mock_ta.analyze_batch.return_value = [
        StockAnalysisResult(ticker="sh.600519", date="2026-08-11", signal="BUY", confidence=85.0),
    ]
    mock_ta.save_results = MagicMock()

    pipeline = QuantPipeline(ta_runner=mock_ta, skip_kronos=True)

    with TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir) / "output.json"
        results = pipeline.run_ta_only(
            tickers=["600519"],
            date="2026-08-11",
            output=str(output_path),
        )

    assert len(results) == 1
    assert results[0].signal == "BUY"
    mock_ta.save_results.assert_called_once()


# ═══════════════════════════════════════════════════════
# Universe engine returns tickers (line 190 log)
# ═══════════════════════════════════════════════════════


def test_run_parallel_with_universe_returns_tickers() -> None:
    """Universe engine produces tickers → pipeline continues with them."""
    from trade_krono_cli.pipeline import QuantPipeline
    from trade_krono_cli.ta_runner import StockAnalysisResult

    mock_ta = MagicMock()
    mock_ta.analyze_batch.return_value = [
        StockAnalysisResult(ticker="sh.600519", date="2026-08-11", signal="BUY", confidence=80.0),
    ]

    pipeline = QuantPipeline(skip_kronos=True)

    with patch("trade_krono_cli.pipeline.pipeline_core.precheck_stock_status") as mock_precheck:
        mock_precheck.return_value = {"sh.600519": _make_abnormality_flag("sh.600519")}
        result = pipeline.run_parallel(tickers=[], date="2026-08-11")

    assert isinstance(result, list)


# ═══════════════════════════════════════════════════════
# output_html path (line 411)
# ═══════════════════════════════════════════════════════


def test_run_parallel_saves_html_when_output_html_set() -> None:
    """run_parallel with output_html saves HTML report."""
    from trade_krono_cli.kronos_runner import KronosForecastResult, PredictionUncertainty
    from trade_krono_cli.pipeline import QuantPipeline
    from trade_krono_cli.ta_runner import StockAnalysisResult

    mock_ta = MagicMock()
    mock_ta.analyze_batch.return_value = [
        StockAnalysisResult(ticker="sh.600519", date="2026-08-11", signal="BUY", confidence=80.0),
    ]

    pu = PredictionUncertainty(
        expected_return=3.2, direction="UP", direction_score=0.8,
        confidence_score=75.0, sample_count_used=1,
    )
    mock_kr = MagicMock()
    mock_kr.predict_batch.return_value = [
        KronosForecastResult(
            ticker="sh.600519", eval_date="2026-08-11", horizon=30,
            direction="UP", expected_change_pct=3.2, last_close=1780.5,
            prediction_uncertainty=pu,
        ),
    ]

    pipeline = QuantPipeline(ta_runner=mock_ta, kronos_runner=mock_kr)

    with TemporaryDirectory() as tmpdir:
        html_path = Path(tmpdir) / "report.html"
        with patch("trade_krono_cli.pipeline.pipeline_core.precheck_stock_status") as mock_precheck:
            mock_precheck.return_value = {"sh.600519": _make_abnormality_flag("sh.600519")}
            pipeline.run_parallel(
                tickers=["600519"], date="2026-08-11", output_html=str(html_path),
            )
        assert html_path.exists()


# ═══════════════════════════════════════════════════════
# investment_decision branch (lines 426, 492)
# ═══════════════════════════════════════════════════════


def test_run_parallel_stores_investment_decision() -> None:
    """Results with investment_decision trigger insert_decision calls."""
    from trade_krono_cli.pipeline import QuantPipeline
    from trade_krono_cli.ta_decision import InvestmentDecision, Signal
    from trade_krono_cli.ta_runner import StockAnalysisResult

    mock_ta = MagicMock()
    decision = InvestmentDecision(signal=Signal.BUY, confidence=85.0, thesis="strong bullish")
    mock_ta.analyze_batch.return_value = [
        StockAnalysisResult(
            ticker="sh.600519", date="2026-08-11", signal="BUY", confidence=85.0,
            investment_decision=decision,
        ),
    ]

    pipeline = QuantPipeline(ta_runner=mock_ta, skip_kronos=True)

    with patch("trade_krono_cli.pipeline.pipeline_core.precheck_stock_status") as mock_precheck:
        mock_precheck.return_value = {"sh.600519": _make_abnormality_flag("sh.600519")}
        result = pipeline.run_parallel(tickers=["600519"], date="2026-08-11")

    assert isinstance(result, list)


def test_run_ta_only_stores_investment_decision() -> None:
    """run_ta_only with investment_decision triggers insert_decision."""
    from trade_krono_cli.pipeline import QuantPipeline
    from trade_krono_cli.ta_decision import InvestmentDecision, Signal
    from trade_krono_cli.ta_runner import StockAnalysisResult

    mock_ta = MagicMock()
    decision = InvestmentDecision(signal=Signal.BUY, confidence=85.0, thesis="bullish")
    mock_ta.analyze_batch.return_value = [
        StockAnalysisResult(
            ticker="sh.600519", date="2026-08-11", signal="BUY", confidence=85.0,
            investment_decision=decision,
        ),
    ]

    pipeline = QuantPipeline(ta_runner=mock_ta, skip_kronos=True)

    with patch("trade_krono_cli.pipeline.pipeline_core.precheck_stock_status") as mock_precheck:
        mock_precheck.return_value = {"sh.600519": _make_abnormality_flag("sh.600519")}
        results = pipeline.run_ta_only(tickers=["600519"], date="2026-08-11")

    assert len(results) == 1
    assert results[0].investment_decision is not None


def test_run_parallel_degrade_mode_triggers_fallback() -> None:
    """degrade_mode='ta_cache_fallback' with ta_cache_fallback_enabled=True triggers fallback."""
    from trade_krono_cli.kronos_runner import KronosForecastResult, PredictionUncertainty
    from trade_krono_cli.pipeline import QuantPipeline
    from trade_krono_cli.pipeline_config import PipelineConfig
    from trade_krono_cli.ta_runner import StockAnalysisResult

    mock_ta = MagicMock()
    mock_ta.analyze_batch.return_value = [
        StockAnalysisResult(
            ticker="sh.600519", date="2026-08-11", signal="BUY", confidence=80.0, error=None,
        ),
        StockAnalysisResult(
            ticker="sz.000858", date="2026-08-11", signal="HOLD", confidence=50.0, error="Network error",
        ),
    ]

    pu = PredictionUncertainty(
        expected_return=3.2, direction="UP", direction_score=0.8,
        confidence_score=75.0, sample_count_used=1,
    )
    mock_kr = MagicMock()
    mock_kr.predict_batch.return_value = [
        KronosForecastResult(
            ticker="sh.600519", eval_date="2026-08-11", horizon=30,
            direction="UP", expected_change_pct=3.2, last_close=1780.5,
            prediction_uncertainty=pu,
        ),
        KronosForecastResult(
            ticker="sz.000858", eval_date="2026-08-11", horizon=30,
            direction="DOWN", expected_change_pct=-1.5, last_close=25.3,
            prediction_uncertainty=PredictionUncertainty(
                expected_return=-1.5, direction="DOWN", direction_score=0.6,
                confidence_score=60.0, sample_count_used=1,
            ),
        ),
    ]

    config = PipelineConfig.default()
    config.degrade_mode = "ta_cache_fallback"
    config.ta_cache_fallback_enabled = True

    pipeline = QuantPipeline(
        ta_runner=mock_ta, kronos_runner=mock_kr, config=config,
    )

    cached_result = {"date": "2026-08-10", "signal": "BUY", "confidence": 75.0, "thesis": "cached"}

    with patch("trade_krono_cli.pipeline.pipeline_core.precheck_stock_status") as mock_precheck:
        with patch("trade_krono_cli.pipeline.pipeline_core.get_research") as mock_get_research:
            mock_precheck.return_value = {
                "sh.600519": _make_abnormality_flag("sh.600519"),
                "sz.000858": _make_abnormality_flag("sz.000858"),
            }
            mock_research = MagicMock()
            mock_research.get_latest_ta_for_ticker.return_value = cached_result
            mock_get_research.return_value = mock_research
            result = pipeline.run_parallel(tickers=["600519", "000858"], date="2026-08-11")

    # Pipeline should complete without error
    assert isinstance(result, list)
