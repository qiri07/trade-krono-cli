"""测试流式流水线（stream_pipeline.py）。"""
import pytest
from unittest.mock import MagicMock, patch
import pandas as pd


def _make_df(rows: int = 400) -> pd.DataFrame:
    return pd.DataFrame({
        "timestamps": pd.date_range("2025-01-01", periods=rows, freq="B"),
        "open":   [100.0] * rows,
        "high":   [101.0] * rows,
        "low":    [99.0] * rows,
        "close":  [100.5] * rows,
        "volume": [1e6] * rows,
        "amount": [1e8] * rows,
    })


def _mock_ta_result(ticker="sh.600519"):
    from trade_krono_cli.ta_runner import StockAnalysisResult
    return StockAnalysisResult(
        ticker=ticker, date="2026-08-12", signal="BUY", confidence=80.0,
    )


def _mock_kr_result(ticker="sh.600519"):
    from trade_krono_cli.kronos_runner import KronosForecastResult
    return KronosForecastResult(
        ticker=ticker, eval_date="2026-08-12", horizon=30,
        direction="UP", expected_change_pct=2.0,
    )


# ── StreamPipeline ────────────────────────────────────────────────────────────

class TestStreamPipeline:
    """StreamPipeline 流式流水线测试。"""

    def _make_pipeline(self, ta_result=None, kr_result=None):
        from trade_krono_cli.pipeline.stream_pipeline import StreamPipeline
        mock_ta = MagicMock()
        mock_ta.analyze_one.return_value = ta_result or _mock_ta_result()
        mock_kr = MagicMock()
        mock_kr.predict_one.return_value = kr_result or _mock_kr_result()
        return StreamPipeline(ta_runner=mock_ta, kronos_runner=mock_kr)

    def test_run_returns_ta_and_kronos_results(self):
        """run() 应返回 (ta_results, kronos_results)。"""
        pipeline = self._make_pipeline()
        with patch("trade_krono_cli.pipeline.stream_pipeline.prepare_kline_batch") as mock_fetch:
            mock_fetch.return_value = {
                "sh.600519": _make_df(400),
                "sz.000858": _make_df(400),
            }
            ta_r, kr_r = pipeline.run(
                tickers=["sh.600519", "sz.000858"],
                date="2026-08-12",
            )
        assert len(ta_r) == 2
        assert len(kr_r) == 2
        assert ta_r[0].signal == "BUY"
        assert kr_r[0].direction == "UP"

    def test_run_injects_prefetched_kline_into_kronos(self):
        """预取的 K 线数据应注入到 KronosRunner._pre_fetched。"""
        from trade_krono_cli.pipeline.stream_pipeline import StreamPipeline
        mock_ta = MagicMock()
        mock_ta.analyze_one.return_value = _mock_ta_result()
        mock_kr = MagicMock()
        mock_kr.predict_one.return_value = _mock_kr_result()
        mock_kr._pre_fetched = {}

        pipeline = StreamPipeline(ta_runner=mock_ta, kronos_runner=mock_kr)
        with patch("trade_krono_cli.pipeline.stream_pipeline.prepare_kline_batch") as mock_fetch:
            mock_fetch.return_value = {"sh.600519": _make_df(400)}
            pipeline.run(tickers=["sh.600519"], date="2026-08-12")

        assert "sh.600519" in mock_kr._pre_fetched

    def test_run_calls_progress_cb(self):
        """progress_cb 应在关键节点被调用。"""
        from trade_krono_cli.pipeline.stream_pipeline import StreamPipeline
        mock_ta = MagicMock()
        mock_ta.analyze_one.return_value = _mock_ta_result()
        mock_kr = MagicMock()
        mock_kr.predict_one.return_value = _mock_kr_result()

        calls = []
        def cb(stage, cur, total):
            calls.append((stage, cur, total))

        pipeline = StreamPipeline(
            ta_runner=mock_ta, kronos_runner=mock_kr, progress_cb=cb,
        )
        with patch("trade_krono_cli.pipeline.stream_pipeline.prepare_kline_batch") as mock_fetch:
            mock_fetch.return_value = {"sh.600519": _make_df(400)}
            pipeline.run(tickers=["sh.600519"], date="2026-08-12")

        assert any(c[0] == "启动" for c in calls)
        assert any(c[0] == "完成" for c in calls)

    def test_run_handles_fetch_failure_gracefully(self):
        """K 线拉取失败时应跳过该股票，不中断其他股票。"""
        pipeline = self._make_pipeline()
        with patch("trade_krono_cli.pipeline.stream_pipeline.prepare_kline_batch") as mock_fetch:
            mock_fetch.return_value = {}  # 无数据
            ta_r, kr_r = pipeline.run(
                tickers=["sh.600519"],
                date="2026-08-12",
            )
        assert len(ta_r) == 1
        assert len(kr_r) == 1
        assert ta_r[0].error is None
        assert kr_r[0].error is None

    def test_run_ta_exception_does_not_crash(self):
        """TA 分析异常时不应中断 Kronos 预测。"""
        from trade_krono_cli.pipeline.stream_pipeline import StreamPipeline
        mock_ta = MagicMock()
        mock_ta.analyze_one.side_effect = RuntimeError("TA crash")
        mock_kr = MagicMock()
        mock_kr.predict_one.return_value = _mock_kr_result()

        pipeline = StreamPipeline(ta_runner=mock_ta, kronos_runner=mock_kr)
        with patch("trade_krono_cli.pipeline.stream_pipeline.prepare_kline_batch") as mock_fetch:
            mock_fetch.return_value = {"sh.600519": _make_df(400)}
            ta_r, kr_r = pipeline.run(tickers=["sh.600519"], date="2026-08-12")

        assert len(ta_r) == 1
        assert ta_r[0].error is not None
        assert kr_r[0].error is None

    def test_run_kronos_exception_does_not_crash(self):
        """Kronos 预测异常时不应中断 TA 分析。"""
        from trade_krono_cli.pipeline.stream_pipeline import StreamPipeline
        mock_ta = MagicMock()
        mock_ta.analyze_one.return_value = _mock_ta_result()
        mock_kr = MagicMock()
        mock_kr.predict_one.side_effect = RuntimeError("Kronos crash")

        pipeline = StreamPipeline(ta_runner=mock_ta, kronos_runner=mock_kr)
        with patch("trade_krono_cli.pipeline.stream_pipeline.prepare_kline_batch") as mock_fetch:
            mock_fetch.return_value = {"sh.600519": _make_df(400)}
            ta_r, kr_r = pipeline.run(tickers=["sh.600519"], date="2026-08-12")

        assert ta_r[0].error is None
        assert len(kr_r) == 1
        assert kr_r[0].error is not None


# ── KronosRunner._prepare 使用 pre-fetched 数据 ─────────────────────────────

class TestKronosRunnerPreFetched:
    """KronosRunner._prepare 预取数据路径测试。"""

    def test_prepare_uses_pre_fetched_when_available(self):
        """_pre_fetched 中有数据时应直接返回，不调用 fetch_lookback。"""
        from trade_krono_cli.kronos_runner import KronosRunner
        from tests.conftest import make_mock_settings

        settings = make_mock_settings(
            kronos_model="kronos-base", kronos_tokenizer="kronos-base",
            kronos_device="cpu", kronos_lookback=400, kronos_pred_len=30,
            kronos_sample_count=1, kronos_T=1.0, kronos_top_p=0.9,
            kronos_use_sample_confidence=False,
        )
        with patch("trade_krono_cli.kronos_runner.KronosRunner._load"):
            runner = KronosRunner(no_cache=True, settings=settings)

        df = _make_df(500)
        runner._pre_fetched["sh.600519"] = df

        with patch("trade_krono_cli.kronos_runner.fetch_lookback") as mock_fetch:
            x_df, x_ts, y_ts, last_close = runner._prepare("sh.600519", "2026-08-12")
            mock_fetch.assert_not_called()

        assert len(x_df) == 400
        assert last_close == 100.5

    def test_prepare_falls_back_to_fetch_lookback(self):
        """_pre_fetched 无数据时应走正常 fetch_lookback 路径。"""
        from trade_krono_cli.kronos_runner import KronosRunner
        from tests.conftest import make_mock_settings

        settings = make_mock_settings(
            kronos_model="kronos-base", kronos_tokenizer="kronos-base",
            kronos_device="cpu", kronos_lookback=400, kronos_pred_len=30,
            kronos_sample_count=1, kronos_T=1.0, kronos_top_p=0.9,
            kronos_use_sample_confidence=False,
        )
        with patch("trade_krono_cli.kronos_runner.KronosRunner._load"):
            runner = KronosRunner(no_cache=True, settings=settings)
            runner._pre_fetched = {}

        mock_df = _make_df(500)
        with patch("trade_krono_cli.kronos_runner.fetch_lookback", return_value=mock_df):
            x_df, x_ts, y_ts, last_close = runner._prepare("sh.600519", "2026-08-12")

        assert len(x_df) == 400


# ── KronosRunner.stream_predict_one ──────────────────────────────────────────

class TestKronosRunnerStreamPredictOne:
    """stream_predict_one 测试。"""

    def test_stream_predict_one_uses_pre_fetched_df(self):
        """stream_predict_one 应直接使用传入的 df，不调用 fetch_lookback。"""
        from trade_krono_cli.kronos_runner import KronosRunner
        from tests.conftest import make_mock_settings

        settings = make_mock_settings(
            kronos_model="kronos-base", kronos_tokenizer="kronos-base",
            kronos_device="cpu", kronos_lookback=400, kronos_pred_len=30,
            kronos_sample_count=1, kronos_T=1.0, kronos_top_p=0.9,
            kronos_use_sample_confidence=False,
        )
        with patch("trade_krono_cli.kronos_runner.KronosRunner._load"):
            runner = KronosRunner(no_cache=True, settings=settings)

        df = _make_df(400)

        with patch("trade_krono_cli.kronos_runner.fetch_lookback") as mock_fetch:
            with patch.object(runner, "_run_predict") as mock_fill:
                mock_fill.return_value = None
                result = runner.stream_predict_one("sh.600519", "2026-08-12", df)
                mock_fetch.assert_not_called()
                mock_fill.assert_called_once()

    def test_stream_predict_one_returns_result(self):
        """stream_predict_one 应返回 KronosForecastResult。"""
        from trade_krono_cli.kronos_runner import KronosRunner, KronosForecastResult
        from tests.conftest import make_mock_settings

        settings = make_mock_settings(
            kronos_model="kronos-base", kronos_tokenizer="kronos-base",
            kronos_device="cpu", kronos_lookback=400, kronos_pred_len=30,
            kronos_sample_count=1, kronos_T=1.0, kronos_top_p=0.9,
            kronos_use_sample_confidence=False,
        )
        with patch("trade_krono_cli.kronos_runner.KronosRunner._load"):
            runner = KronosRunner(no_cache=True, settings=settings)

        df = _make_df(400)

        with patch.object(runner, "_run_predict") as mock_fill:
            mock_fill.return_value = None
            result = runner.stream_predict_one("sh.600519", "2026-08-12", df)

        assert isinstance(result, KronosForecastResult)
        assert result.ticker == "sh.600519"
        assert result.eval_date == "2026-08-12"


# ── QuantPipeline.run_parallel streaming 分支 ───────────────────────────────

class TestQuantPipelineStreaming:
    """QuantPipeline.run_parallel(streaming=True) 测试。"""

    def test_streaming_mode_uses_stream_pipeline(self):
        """streaming=True 时应走 StreamPipeline 路径。"""
        from trade_krono_cli.pipeline import QuantPipeline
        from trade_krono_cli.ta_runner import StockAnalysisResult
        from trade_krono_cli.kronos_runner import KronosForecastResult, PredictionUncertainty

        mock_ta = MagicMock()
        mock_ta.analyze_batch.return_value = [
            StockAnalysisResult(ticker="sh.600519", date="2026-08-12", signal="BUY", confidence=80.0),
        ]
        pu = PredictionUncertainty(
            expected_return=2.0, direction="UP", direction_confidence=0.8,
            confidence_score=75.0, sample_count_used=1,
        )
        mock_kr = MagicMock()
        mock_kr.predict_batch.return_value = [
            KronosForecastResult(
                ticker="sh.600519", eval_date="2026-08-12", horizon=30,
                direction="UP", expected_change_pct=2.0, prediction_uncertainty=pu,
            ),
        ]

        with patch("trade_krono_cli.pipeline.stream_pipeline.StreamPipeline") as MockStream:
            mock_stream_instance = MagicMock()
            mock_stream_instance.run.return_value = (
                [_mock_ta_result()],
                [_mock_kr_result()],
            )
            MockStream.return_value = mock_stream_instance

            pipeline = QuantPipeline(
                ta_runner=mock_ta, kronos_runner=mock_kr, skip_kronos=False,
            )
            merged = pipeline.run_parallel(
                tickers=["sh.600519"],
                date="2026-08-12",
                streaming=True,
            )

            MockStream.assert_called_once()
            mock_stream_instance.run.assert_called_once()
            assert len(merged) >= 1

    def test_non_streaming_unchanged(self):
        """streaming=False（默认）应保持原有并行路径行为。"""
        from trade_krono_cli.pipeline import QuantPipeline
        from trade_krono_cli.ta_runner import StockAnalysisResult
        from trade_krono_cli.kronos_runner import KronosForecastResult, PredictionUncertainty

        mock_ta = MagicMock()
        mock_ta.analyze_batch.return_value = [
            StockAnalysisResult(ticker="sh.600519", date="2026-08-12", signal="BUY", confidence=80.0),
        ]
        pu = PredictionUncertainty(
            expected_return=2.0, direction="UP", direction_confidence=0.8,
            confidence_score=75.0, sample_count_used=1,
        )
        mock_kr = MagicMock()
        mock_kr.predict_batch.return_value = [
            KronosForecastResult(
                ticker="sh.600519", eval_date="2026-08-12", horizon=30,
                direction="UP", expected_change_pct=2.0, prediction_uncertainty=pu,
            ),
        ]

        pipeline = QuantPipeline(
            ta_runner=mock_ta, kronos_runner=mock_kr, skip_kronos=False,
        )
        merged = pipeline.run_parallel(
            tickers=["sh.600519"],
            date="2026-08-12",
            streaming=False,
        )
        assert len(merged) >= 1
        assert merged[0]["ticker"] == "sh.600519"
