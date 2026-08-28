"""Pipeline 集成测试 — 验证 orchestrator 与 scorer/reporter 协作。"""

from pathlib import Path
from unittest.mock import MagicMock


class TestPipelineOrchestrator:
    """QuantPipeline 集成测试（mock TA + Kronos）。"""

    def test_run_parallel_full_flow(self):
        """完整并行流程：TA + Kronos → merge → score → save。"""
        from trade_krono_cli.kronos_runner import KronosForecastResult, PredictionUncertainty
        from trade_krono_cli.pipeline import QuantPipeline
        from trade_krono_cli.ta_runner import StockAnalysisResult

        mock_ta = MagicMock()
        mock_ta.analyze_batch.return_value = [
            StockAnalysisResult(
                ticker="sh.600519", date="2026-08-12", signal="BUY", confidence=80.0
            ),
        ]

        pu = PredictionUncertainty(
            expected_return=3.0,
            direction="UP",
            direction_score=0.8,
            volatility=1.0,
            path_dispersion=None,
            confidence_score=75.0,
        )
        mock_kr = MagicMock()
        mock_kr.predict_batch.return_value = [
            KronosForecastResult(
                ticker="sh.600519",
                eval_date="2026-08-12",
                horizon=30,
                direction="UP",
                expected_change_pct=3.0,
                last_close=1780.0,
                prediction_uncertainty=pu,
            ),
        ]

        pipeline = QuantPipeline(ta_runner=mock_ta, kronos_runner=mock_kr, no_cache=True)
        merged = pipeline.run_parallel(
            tickers=["600519"],
            date="2026-08-12",
            output_json="/tmp/test_integration_parallel.json",
        )

        assert len(merged) >= 1
        assert merged[0]["ticker"] == "sh.600519"
        assert merged[0]["ta_signal"] == "BUY"
        assert merged[0]["kronos_direction"] == "UP"
        assert "composite_score" in merged[0]
        assert "rank" in merged[0]

    def test_run_ta_only(self):
        """仅 TA 模式。"""
        from trade_krono_cli.pipeline import QuantPipeline
        from trade_krono_cli.ta_runner import StockAnalysisResult

        mock_ta = MagicMock()
        mock_ta.analyze_batch.return_value = [
            StockAnalysisResult(
                ticker="sh.600519", date="2026-08-12", signal="BUY", confidence=85.0
            ),
        ]

        pipeline = QuantPipeline(ta_runner=mock_ta, skip_kronos=True, no_cache=True)
        results = pipeline.run_ta_only(tickers=["600519"], date="2026-08-12")
        assert len(results) == 1
        assert results[0].signal == "BUY"

    def test_run_kronos_only(self):
        """仅 Kronos 模式。"""
        from trade_krono_cli.kronos_runner import KronosForecastResult
        from trade_krono_cli.pipeline import QuantPipeline

        mock_ta = MagicMock()
        mock_ta.analyze_batch.return_value = []

        mock_kr = MagicMock()
        mock_kr.predict_batch.return_value = [
            KronosForecastResult(
                ticker="sh.600519",
                eval_date="2026-08-12",
                horizon=30,
                direction="UP",
                expected_change_pct=2.5,
            ),
        ]

        pipeline = QuantPipeline(ta_runner=mock_ta, kronos_runner=mock_kr, no_cache=True)
        results = pipeline.run_kronos_only(tickers=["600519"], date="2026-08-12")
        assert len(results) == 1
        assert results[0].direction == "UP"

    def test_kronos_thread_exception_handled(self):
        """Kronos 线程异常不应中断 TA 结果。"""
        from trade_krono_cli.pipeline import QuantPipeline
        from trade_krono_cli.ta_runner import StockAnalysisResult

        mock_ta = MagicMock()
        mock_ta.analyze_batch.return_value = [
            StockAnalysisResult(
                ticker="sh.600519", date="2026-08-12", signal="BUY", confidence=80.0
            ),
        ]
        mock_kr = MagicMock()
        mock_kr.predict_batch.side_effect = RuntimeError("model load failed")

        pipeline = QuantPipeline(ta_runner=mock_ta, kronos_runner=mock_kr, no_cache=True)
        merged = pipeline.run_parallel(tickers=["600519"], date="2026-08-12")
        assert len(merged) >= 1
        assert merged[0]["ta_signal"] == "BUY"

    def test_filter_pool_reduces_results(self):
        """filter_pool 应正确过滤低置信度股票。"""
        from trade_krono_cli.pipeline import QuantPipeline
        from trade_krono_cli.ta_runner import StockAnalysisResult

        mock_ta = MagicMock()
        mock_ta.analyze_batch.return_value = [
            StockAnalysisResult(
                ticker="sh.600519", date="2026-08-12", signal="BUY", confidence=80.0
            ),
            StockAnalysisResult(
                ticker="sz.000858", date="2026-08-12", signal="BUY", confidence=40.0
            ),
            StockAnalysisResult(
                ticker="sh.600036", date="2026-08-12", signal="SELL", confidence=90.0
            ),
        ]
        mock_kr = MagicMock()
        mock_kr.predict_batch.return_value = []

        pipeline = QuantPipeline(ta_runner=mock_ta, kronos_runner=mock_kr, no_cache=True)
        merged = pipeline.run_parallel(tickers=["600519", "000858", "600036"], date="2026-08-12")
        tickers = {m["ticker"] for m in merged}
        assert "sh.600519" in tickers
        assert "sz.000858" not in tickers
        assert "sh.600036" not in tickers

    def test_empty_tickers(self):
        """空 ticker 列表应返回空结果。"""
        from trade_krono_cli.pipeline import QuantPipeline

        mock_ta = MagicMock()
        mock_ta.analyze_batch.return_value = []
        mock_kr = MagicMock()
        mock_kr.predict_batch.return_value = []

        pipeline = QuantPipeline(ta_runner=mock_ta, kronos_runner=mock_kr, no_cache=True)
        merged = pipeline.run_parallel(tickers=[], date="2026-08-12")
        assert merged == []


class TestScorer:
    """pipeline scorer 测试（通过 default_scorer 直接验证）。"""

    def test_default_scorer_ranking(self):
        """高 TA 置信度应排在负收益股票之前。"""
        from trade_krono_cli.pipeline.merge import default_scorer

        items = [
            {
                "ticker": "A",
                "ta_confidence": 60,
                "kronos_change_pct": 1.0,
                "kronos_direction": "UP",
            },
            {
                "ticker": "B",
                "ta_confidence": 80,
                "kronos_change_pct": 3.0,
                "kronos_direction": "UP",
            },
            {
                "ticker": "C",
                "ta_confidence": 90,
                "kronos_change_pct": -2.0,
                "kronos_direction": "DOWN",
            },
        ]
        # 按分数降序排列
        ranked = sorted(items, key=lambda x: default_scorer(x), reverse=True)
        # A 分数: 0.4*60 + 0.3*51 + 0.1*10 = 24+15.3+1 = 40.3
        # B 分数: 0.4*80 + 0.3*53 + 0.1*10 = 32+15.9+1 = 48.9
        # C 分数: 0.4*90 + 0.3*48 + 0.1*(-10) = 36+14.4-1 = 49.4
        # C 应排第一（高 TA 置信度抵消了负收益）
        assert ranked[0]["ticker"] == "C"
        assert ranked[1]["ticker"] == "B"
        assert ranked[2]["ticker"] == "A"

    def test_custom_scorer(self):
        """自定义打分函数可覆盖默认逻辑。"""

        items = [{"ticker": "A", "score": 10}, {"ticker": "B", "score": 20}]

        def custom_scorer(item):
            return item.get("score", 0) * 2

        ranked = sorted(items, key=custom_scorer, reverse=True)
        assert ranked[0]["ticker"] == "B"
        assert ranked[0]["score"] * 2 == 40


class TestReporter:
    """pipeline reporter 测试。"""

    def test_save_json_report(self, tmp_path):
        from trade_krono_cli.pipeline.reporter import save_json_report

        merged = [
            {"ticker": "sh.600519", "rank": 1, "composite_score": 75.0},
        ]
        path = str(tmp_path / "report.json")
        result = save_json_report(merged, path)
        assert result == path
        assert Path(path).exists()
        import json

        with open(path) as f:
            data = json.load(f)
        # 新项目格式：顶层 dict，results 为实际报告列表
        assert data.get("project") == "trade-krono-cli"
        assert data["count"] == 1
        assert len(data["results"]) == 1
        assert data["results"][0]["ticker"] == "sh.600519"

    def test_save_html_report(self, tmp_path):
        from trade_krono_cli.pipeline.reporter import save_html_report

        merged = [
            {"ticker": "sh.600519", "rank": 1, "composite_score": 75.0, "ta_signal": "BUY"},
        ]
        path = str(tmp_path / "report.html")
        result = save_html_report(merged, path, "2026-08-12")
        assert result == path
        assert Path(path).exists()

    def test_print_results_table_no_error(self):
        """print_results_table 不应抛异常。"""
        from trade_krono_cli.pipeline.reporter import print_results_table

        merged = [
            {"ticker": "sh.600519", "rank": 1, "composite_score": 75.0},
        ]
        print_results_table(merged)

    def test_print_results_summary_no_error(self):
        """print_results_summary 不应抛异常。"""
        from trade_krono_cli.pipeline.reporter import print_results_summary

        merged = [
            {"ticker": "sh.600519", "rank": 1, "composite_score": 75.0},
        ]
        print_results_summary(merged, "2026-08-12")


class TestDataFetcher:
    """pipeline data_fetcher 测试。"""

    def test_fetch_stock_quote_returns_dict(self):
        from trade_krono_cli.pipeline.data_fetcher import fetch_stock_quote

        result = fetch_stock_quote("sh.600519")
        assert isinstance(result, dict)
