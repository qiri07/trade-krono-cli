"""测试 StockAnalysisResult、DecisionAdapter 及 TradingAgentsRunner 内部方法。"""
import pytest
from unittest.mock import MagicMock, patch


def _make_ta_runner():
    from trade_krono_cli.ta_runner import TradingAgentsRunner
    with patch("trade_krono_cli.ta_runner.get_settings") as mock_settings:
        s = mock_settings.return_value
        s.llm_provider = "deepseek"
        s.deep_think_llm = "deepseek-chat"
        s.quick_think_llm = "deepseek-chat"
        s.max_debate_rounds = 1
        s.max_risk_discuss_rounds = 1
        s.cache_dir = MagicMock()
        s.results_dir = MagicMock()
        s.memory_log_path = MagicMock()
        return TradingAgentsRunner(no_cache=True, safe_mode=False)


class TestStockAnalysisResult:
    """StockAnalysisResult 数据类测试。"""

    def test_default_init(self):
        from trade_krono_cli.ta_runner import StockAnalysisResult
        r = StockAnalysisResult(ticker="sh.600519", date="2026-08-12")
        assert r.ticker == "sh.600519"
        assert r.signal is None
        assert r.error is None
        assert r.reports == {}
        assert r.reports_raw == {}

    def test_to_dict(self):
        from trade_krono_cli.ta_runner import StockAnalysisResult
        r = StockAnalysisResult(
            ticker="sh.600519", date="2026-08-12",
            signal="BUY", confidence=80.0,
            reasoning="strong momentum",
        )
        d = r.to_dict()
        assert d["ticker"] == "sh.600519"
        assert d["signal"] == "BUY"
        assert d["confidence"] == 80.0

    def test_decision_property_with_investment_decision(self):
        """当 investment_decision 存在时，decision 返回它。"""
        from trade_krono_cli.ta_runner import StockAnalysisResult
        from trade_krono_cli.ta_decision import InvestmentDecision, Signal
        inv = InvestmentDecision(signal=Signal.BUY, confidence=85.0, thesis="test")
        r = StockAnalysisResult(ticker="sh.600519", date="2026-08-12", investment_decision=inv)
        assert r.decision.signal == Signal.BUY
        assert r.decision.confidence == 85.0

    def test_decision_property_fallback(self):
        """无 investment_decision 时 fallback 到 legacy 字段。"""
        from trade_krono_cli.ta_runner import StockAnalysisResult
        r = StockAnalysisResult(ticker="sh.600519", date="2026-08-12", signal="HOLD", confidence=60.0)
        d = r.decision
        assert d.signal.value == "HOLD"
        assert d.confidence == 60.0

    def test_is_buy_true(self):
        from trade_krono_cli.ta_runner import StockAnalysisResult
        r = StockAnalysisResult(ticker="sh.600519", date="2026-08-12", signal="BUY", confidence=80.0)
        assert r.is_buy(min_confidence=55.0) is True

    def test_is_buy_false_low_confidence(self):
        from trade_krono_cli.ta_runner import StockAnalysisResult
        r = StockAnalysisResult(ticker="sh.600519", date="2026-08-12", signal="BUY", confidence=50.0)
        assert r.is_buy(min_confidence=55.0) is False

    def test_is_buy_false_not_buy(self):
        from trade_krono_cli.ta_runner import StockAnalysisResult
        r = StockAnalysisResult(ticker="sh.600519", date="2026-08-12", signal="HOLD", confidence=80.0)
        assert r.is_buy(min_confidence=55.0) is False

    def test_is_buy_false_with_error(self):
        from trade_krono_cli.ta_runner import StockAnalysisResult
        r = StockAnalysisResult(ticker="sh.600519", date="2026-08-12", signal="BUY", confidence=80.0, error="some error")
        assert r.is_buy(min_confidence=55.0) is False


class TestTradingAgentsRunnerExtractReports:
    """_extract_reports 测试。"""

    def test_extract_all_reports(self):
        runner = _make_ta_runner()
        state = {
            "market_report": "short market analysis text",
            "sentiment_report": "bullish sentiment",
            "news_report": "positive news flow",
            "fundamentals_report": "strong earnings",
            "policy_report": "supportive policy",
            "hot_money_report": "institutional buying",
            "lockup_report": "no lockup pressure",
        }
        raw, summary = runner._extract_reports(state)
        assert len(raw) == 7
        assert len(summary) == 7
        assert len(summary["market"]) <= 500
        assert summary["market"].startswith("short market analysis text")

    def test_extract_skips_empty(self):
        runner = _make_ta_runner()
        state = {
            "market_report": "some text",
            "sentiment_report": None,
            "news_report": "",
        }
        raw, summary = runner._extract_reports(state)
        assert "market" in raw
        assert "sentiment" not in raw
        assert "news" not in raw

    def test_extract_non_string_value(self):
        """非字符串值应被 json.dumps 序列化。"""
        runner = _make_ta_runner()
        state = {
            "market_report": {"key": "value"},
        }
        raw, summary = runner._extract_reports(state)
        assert "market" in raw
        assert '"key"' in raw["market"]


class TestTradingAgentsRunnerExtractDecision:
    """_extract_decision 测试。"""

    def test_extract_from_final_trade_decision(self):
        runner = _make_ta_runner()
        state = {
            "final_trade_decision": "BUY at 100.5 with high confidence",
        }
        legacy, inv_decision = runner._extract_decision(state)
        assert legacy["signal"] is not None
        assert inv_decision is not None
        assert "BUY" in inv_decision.thesis.upper() or "buy" in inv_decision.thesis.lower()

    def test_extract_from_investment_plan(self):
        """fallback: 使用 investment_plan 字段。"""
        runner = _make_ta_runner()
        state = {
            "investment_plan": "HOLD for now",
        }
        legacy, inv_decision = runner._extract_decision(state)
        assert inv_decision is not None

    def test_extract_from_debate_state(self):
        """辩论状态中的决策应优先使用。"""
        runner = _make_ta_runner()
        state = {
            "final_trade_decision": "initial decision",
            "investment_debate_state": {
                "final_decision": "debated BUY decision",
            },
        }
        legacy, inv_decision = runner._extract_decision(state)
        assert "debated" in inv_decision.thesis.lower() or "debated" in inv_decision.thesis.upper()

    def test_extract_empty_state(self):
        """空 state 应返回默认 HOLD 决策。"""
        runner = _make_ta_runner()
        legacy, inv_decision = runner._extract_decision({})
        assert inv_decision is not None
        assert legacy["signal"] == "HOLD"


class TestTradingAgentsRunnerCacheHit:
    """analyze_one 缓存路径测试。"""

    def test_cache_hit_returns_fast(self):
        """缓存命中时应直接返回，elapsed_sec=0。"""
        from trade_krono_cli.ta_runner import TradingAgentsRunner
        runner = _make_ta_runner()
        runner._cache = MagicMock()
        runner._cache.get_ta.return_value = {
            "ticker": "sh.600519", "date": "2026-08-12",
            "signal": "BUY", "confidence": 80.0,
            "reasoning": "strong momentum",
            "investment_decision": {
                "signal": "BUY", "confidence": 80.0,
                "thesis": "strong momentum", "risks": "",
            },
        }
        result = runner.analyze_one("sh.600519", "2026-08-12")
        assert result.signal == "BUY"
        assert result.confidence == 80.0
        assert result.error is None
        assert result.elapsed_sec == 0.0
        assert result.investment_decision is not None

    def test_cache_miss_calls_get_graph(self):
        """缓存未命中时应尝试加载 graph。"""
        from trade_krono_cli.ta_runner import TradingAgentsRunner
        runner = _make_ta_runner()
        runner._cache = MagicMock()
        runner._cache.get_ta.return_value = None

        with patch.object(runner, "_get_graph") as mock_graph:
            mock_graph.side_effect = RuntimeError("graph init failed")
            result = runner.analyze_one("sh.600519", "2026-08-12")
            assert result.error is not None
            assert "RuntimeError" in result.error


class TestTradingAgentsRunnerAnalyzeError:
    """analyze_one 异常路径测试。"""

    def test_analysis_failure_sets_error(self):
        """分析过程失败时应记录 error。"""
        from trade_krono_cli.ta_runner import TradingAgentsRunner
        runner = _make_ta_runner()
        with patch.object(runner, "_get_graph") as mock_graph:
            mock_graph.side_effect = RuntimeError("graph init failed")
            result = runner.analyze_one("sh.600519", "2026-08-12")
            assert result.error is not None
            assert result.ticker == "sh.600519"
            assert result.elapsed_sec >= 0

    def test_validation_error_raises(self):
        """无效 ticker 应抛出 ValueError（validate_ticker 在 try 之前调用）。"""
        from trade_krono_cli.ta_runner import TradingAgentsRunner
        runner = _make_ta_runner()
        with pytest.raises(ValueError, match="无效股票代码"):
            runner.analyze_one("invalid_ticker", "2026-08-12")


class TestTradingAgentsRunnerSaveResults:
    """save_results 测试。"""

    def test_saves_json_file(self, tmp_path):
        from trade_krono_cli.ta_runner import TradingAgentsRunner, StockAnalysisResult
        runner = _make_ta_runner()
        results = [
            StockAnalysisResult(ticker="sh.600519", date="2026-08-12", signal="BUY", confidence=80.0),
            StockAnalysisResult(ticker="sz.000858", date="2026-08-12", signal="HOLD", confidence=60.0),
        ]
        path = str(tmp_path / "results.json")
        returned = runner.save_results(results, path)
        assert returned == path
        import json
        with open(path) as f:
            data = json.load(f)
        assert len(data) == 2
        assert data[0]["ticker"] == "sh.600519"
