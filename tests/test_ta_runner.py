"""测试 StockAnalysisResult、DecisionAdapter 及 TradingAgentsRunner 内部方法。"""

from unittest.mock import MagicMock, patch

import pytest


def _make_ta_runner(settings=None):
    from tests.conftest import make_mock_settings
    from trade_krono_cli.ta_runner import TradingAgentsRunner

    if settings is None:
        settings = make_mock_settings(
            llm_provider="deepseek",
            deep_think_llm="deepseek-chat",
            quick_think_llm="deepseek-chat",
            max_debate_rounds=1,
            max_risk_discuss_rounds=1,
        )
    return TradingAgentsRunner(no_cache=True, safe_mode=False, settings=settings)


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
            ticker="sh.600519",
            date="2026-08-12",
            signal="BUY",
            confidence=80.0,
            reasoning="strong momentum",
        )
        d = r.to_dict()
        assert d["ticker"] == "sh.600519"
        assert d["signal"] == "BUY"
        assert d["confidence"] == 80.0

    def test_decision_property_with_investment_decision(self):
        """当 investment_decision 存在时，decision 返回它。"""
        from trade_krono_cli.ta_decision import InvestmentDecision, Signal
        from trade_krono_cli.ta_runner import StockAnalysisResult

        inv = InvestmentDecision(signal=Signal.BUY, confidence=85.0, thesis="test")
        r = StockAnalysisResult(ticker="sh.600519", date="2026-08-12", investment_decision=inv)
        assert r.decision.signal == Signal.BUY
        assert r.decision.confidence == 85.0

    def test_decision_property_fallback(self):
        """无 investment_decision 时 fallback 到 legacy 字段。"""
        from trade_krono_cli.ta_runner import StockAnalysisResult

        r = StockAnalysisResult(
            ticker="sh.600519", date="2026-08-12", signal="HOLD", confidence=60.0
        )
        d = r.decision
        assert d.signal.value == "HOLD"
        assert d.confidence == 60.0

    def test_is_buy_true(self):
        from trade_krono_cli.ta_runner import StockAnalysisResult

        r = StockAnalysisResult(
            ticker="sh.600519", date="2026-08-12", signal="BUY", confidence=80.0
        )
        assert r.is_buy(min_confidence=55.0) is True

    def test_is_buy_false_low_confidence(self):
        from trade_krono_cli.ta_runner import StockAnalysisResult

        r = StockAnalysisResult(
            ticker="sh.600519", date="2026-08-12", signal="BUY", confidence=50.0
        )
        assert r.is_buy(min_confidence=55.0) is False

    def test_is_buy_false_not_buy(self):
        from trade_krono_cli.ta_runner import StockAnalysisResult

        r = StockAnalysisResult(
            ticker="sh.600519", date="2026-08-12", signal="HOLD", confidence=80.0
        )
        assert r.is_buy(min_confidence=55.0) is False

    def test_is_buy_false_with_error(self):
        from trade_krono_cli.ta_runner import StockAnalysisResult

        r = StockAnalysisResult(
            ticker="sh.600519", date="2026-08-12", signal="BUY", confidence=80.0, error="some error"
        )
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
        runner = _make_ta_runner()
        runner._cache = MagicMock()
        runner._cache.get_ta.return_value = {
            "ticker": "sh.600519",
            "date": "2026-08-12",
            "signal": "BUY",
            "confidence": 80.0,
            "reasoning": "strong momentum",
            "investment_decision": {
                "signal": "BUY",
                "confidence": 80.0,
                "thesis": "strong momentum",
                "risks": "",
            },
        }
        result = runner.analyze_one("sh.600519", "2026-08-12")
        assert result.signal == "BUY"
        assert result.confidence == 80.0
        assert result.error is None
        assert result.elapsed_sec == 0.0
        assert result.investment_decision is not None

    def test_cache_miss_calls_adapter(self):
        """缓存未命中时应调用 adapter。"""
        runner = _make_ta_runner()
        runner._cache = MagicMock()
        runner._cache.get_ta.return_value = None

        mock_adapter = MagicMock()
        mock_adapter.build_config.return_value = {"trade_date": "2026-08-12"}
        mock_adapter.run_analysis.return_value = {
            "success": True,
            "final_state": {
                "final_trade_decision": "BUY",
                "market_report": "text",
            },
        }
        # 通过设置 _session 来提供 adapter（避免 patch.object 对 property 失效）
        mock_session = MagicMock()
        mock_session.adapter = mock_adapter
        runner._session = mock_session
        _result = runner.analyze_one("sh.600519", "2026-08-12")
        mock_adapter.run_analysis.assert_called_once()


class TestTradingAgentsRunnerAnalyzeError:
    """analyze_one 异常路径测试。"""

    def test_analysis_failure_sets_error(self):
        """分析过程失败时应记录 error。"""
        runner = _make_ta_runner()
        mock_adapter = MagicMock()
        mock_adapter.run_analysis.side_effect = RuntimeError("adapter init failed")
        mock_session = MagicMock()
        mock_session.adapter = mock_adapter
        runner._session = mock_session
        result = runner.analyze_one("sh.600519", "2026-08-12")
        assert result.error is not None
        assert result.ticker == "sh.600519"
        assert result.elapsed_sec >= 0

    def test_validation_error_raises(self):
        """无效 ticker 应抛出 ValueError（validate_ticker 在 try 之前调用）。"""
        runner = _make_ta_runner()
        with pytest.raises(ValueError, match="无效股票代码"):
            runner.analyze_one("invalid_ticker", "2026-08-12")


class TestTradingAgentsRunnerSaveResults:
    """save_results 测试。"""

    def test_saves_json_file(self, tmp_path):
        from trade_krono_cli.ta_runner import StockAnalysisResult

        runner = _make_ta_runner()
        results = [
            StockAnalysisResult(
                ticker="sh.600519", date="2026-08-12", signal="BUY", confidence=80.0
            ),
            StockAnalysisResult(
                ticker="sz.000858", date="2026-08-12", signal="HOLD", confidence=60.0
            ),
        ]
        path = str(tmp_path / "results.json")
        returned = runner.save_results(results, path)
        assert returned == path
        import json

        with open(path) as f:
            data = json.load(f)
        # 新项目格式：顶层含 project 字段，结果在 indices 1..N
        assert data[0].get("project") == "trade-krono-cli"
        results = data[1:]
        assert len(results) == 2
        assert results[0]["ticker"] == "sh.600519"


class TestTradingAgentsRunnerBuildConfig:
    """_build_config 测试。"""

    def test_build_config_default(self):
        runner = _make_ta_runner()
        cfg = runner._build_config()
        assert cfg["llm_provider"] == "deepseek"
        assert cfg["deep_think_llm"] == "deepseek-chat"
        assert cfg["output_language"] == "Chinese"
        assert cfg["max_debate_rounds"] == 1

    def test_build_config_with_backend_url(self):
        runner = _make_ta_runner()
        runner.backend_url = "http://test:8080"
        cfg = runner._build_config()
        assert cfg["backend_url"] == "http://test:8080"

    def test_build_config_custom_params(self):
        runner = _make_ta_runner()
        runner.llm_provider = "openai"
        runner.deep_think_llm = "gpt-4"
        cfg = runner._build_config()
        assert cfg["llm_provider"] == "openai"
        assert cfg["deep_think_llm"] == "gpt-4"


class TestTradingAgentsRunnerValidateProvider:
    """_validate_provider 测试（仅在无 session 时生效）。"""

    def test_no_available_providers_raises(self):
        from tests.conftest import make_mock_settings
        from trade_krono_cli.ta_runner import TradingAgentsRunner

        settings = make_mock_settings(llm_provider="deepseek")
        # patch 必须在 runner 构造前生效，否则 safe_mode=True 触发 __init__ 时
        # _validate_provider() 会读取真实 KeyVault 并因无密钥而抛出
        with patch("trade_krono_cli.security.KeyVault") as mock_vault:
            mock_vault.return_value.available_providers.return_value = []
            with pytest.raises(RuntimeError, match="未检测到任何 LLM API 密钥"):
                TradingAgentsRunner(safe_mode=True, settings=settings)

    def test_provider_not_available_falls_back(self):
        from tests.conftest import make_mock_settings
        from trade_krono_cli.ta_runner import TradingAgentsRunner

        settings = make_mock_settings(llm_provider="nonexistent")
        # 模拟配置提供商无密钥，但有其他可用提供商时自动回退
        with patch("trade_krono_cli.security.KeyVault") as mock_vault:
            mock_vault.return_value.available_providers.return_value = ["openai"]
            runner = TradingAgentsRunner(safe_mode=True, settings=settings)
            assert runner.llm_provider == "openai"


class TestTradingAgentsRunnerAdapter:
    """adapter 属性测试。"""

    def test_adapter_creates_new_when_no_session(self):
        """无 session 时 adapter 属性创建新实例。"""
        runner = _make_ta_runner()
        with patch("trade_krono_cli.ta_runner.TradingAgentsAdapterImpl") as MockAdapter:
            mock_adapter = MagicMock()
            MockAdapter.return_value = mock_adapter
            adapter = runner.adapter
            assert adapter is mock_adapter
            MockAdapter.assert_called_once()

    def test_adapter_uses_session_when_available(self):
        """有 session 时 adapter 从 session 获取。"""
        from trade_krono_cli.ta_runner import TradingAgentsRunner

        mock_session = MagicMock()
        mock_session.adapter = "session_adapter"
        runner = TradingAgentsRunner(session=mock_session)
        assert runner.adapter == "session_adapter"


class TestTradingAgentsRunnerAnalyzeBatch:
    """analyze_batch 测试。"""

    def test_analyze_batch_calls_analyze_one(self):
        runner = _make_ta_runner()
        results = runner.analyze_batch(["sh.600519", "sz.000858"], "2026-08-12")
        assert len(results) == 2
        assert all(r.ticker in ("sh.600519", "sz.000858") for r in results)

    def test_analyze_batch_with_progress_cb(self):
        runner = _make_ta_runner()
        callbacks = []

        def cb(idx, total, result):
            callbacks.append((idx, total))

        _results = runner.analyze_batch(["sh.600519"], "2026-08-12", progress_cb=cb)
        assert len(callbacks) == 1
        assert callbacks[0] == (1, 1)

    def test_analyze_batch_progress_cb_raises(self):
        """progress_cb 抛异常时不应中断批处理。"""
        runner = _make_ta_runner()

        def bad_cb(idx, total, result):
            raise RuntimeError("cb error")

        results = runner.analyze_batch(["sh.600519"], "2026-08-12", progress_cb=bad_cb)
        assert len(results) == 1
        assert results[0].ticker == "sh.600519"


class TestTradingAgentsRunnerSaveRawReports:
    """save_raw_reports 测试。"""

    def test_save_raw_reports_writes_files(self, tmp_path):
        from trade_krono_cli.ta_runner import StockAnalysisResult

        runner = _make_ta_runner()
        results = [
            StockAnalysisResult(
                ticker="sh.600519",
                date="2026-08-12",
                signal="BUY",
                confidence=80.0,
                reports_raw={"market": "full market report text"},
                reasoning="strong momentum",
                risk_assessment="low risk",
            ),
            StockAnalysisResult(
                ticker="sz.000858",
                date="2026-08-12",
                signal="HOLD",
                error="network error",  # 有 error 应跳过
            ),
        ]
        written = runner.save_raw_reports(results, "2026-08-12", results_dir=tmp_path)
        assert "sh.600519" in written
        assert "sz.000858" not in written
        # 验证文件内容
        import json

        file_path = tmp_path / "raw" / "2026-08-12" / "sh.600519.json"
        with open(file_path) as f:
            data = json.load(f)
        assert data["ticker"] == "sh.600519"
        assert data["reports_raw"]["market"] == "full market report text"


class TestTradingAgentsRunnerLoadRawReport:
    """load_raw_report 静态方法测试。"""

    def test_load_existing_report(self, tmp_path):
        import json

        raw_dir = tmp_path / "raw" / "2026-08-12"
        raw_dir.mkdir(parents=True)
        report_path = raw_dir / "sh.600519.json"
        with open(report_path, "w") as f:
            json.dump({"ticker": "sh.600519", "date": "2026-08-12"}, f)

        from trade_krono_cli.ta_runner import TradingAgentsRunner

        result = TradingAgentsRunner.load_raw_report(
            "sh.600519", "2026-08-12", results_dir=tmp_path
        )
        assert result is not None
        assert result["ticker"] == "sh.600519"

    def test_load_missing_report_returns_none(self, tmp_path):
        from trade_krono_cli.ta_runner import TradingAgentsRunner

        result = TradingAgentsRunner.load_raw_report(
            "sh.600519", "2026-08-12", results_dir=tmp_path
        )
        assert result is None
