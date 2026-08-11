"""测试 TA 决策标准化适配器（DecisionAdapter + InvestmentDecision）。"""
import pytest
from pathlib import Path
from trade_krono_cli.ta_decision import InvestmentDecision, Signal, DecisionAdapter
from trade_krono_cli.ta_runner import StockAnalysisResult


@pytest.fixture
def adapter():
    return DecisionAdapter()


# ── Rating 结构化解析 ────────────────────────────────────────────────────────

def test_rating_structured_underweight(adapter):
    text = "**Rating**: Underweight\n**Executive Summary**: ..."
    dec = adapter.parse(text)
    assert dec.signal == Signal.SELL
    assert dec.confidence == 40.0
    assert dec.thesis != ""


def test_rating_structured_strong_buy(adapter):
    text = "**Rating**: Strong Buy\n**Summary**: ..."
    dec = adapter.parse(text)
    assert dec.signal == Signal.BUY
    assert dec.confidence == 95.0


def test_rating_structured_buy(adapter):
    text = "**Rating**: Buy"
    dec = adapter.parse(text)
    assert dec.signal == Signal.BUY
    assert dec.confidence == 80.0


def test_rating_structured_neutral(adapter):
    text = "**Rating**: Neutral"
    dec = adapter.parse(text)
    assert dec.signal == Signal.HOLD
    assert dec.confidence == 50.0


def test_rating_structured_sell(adapter):
    text = "**Rating**: Sell"
    dec = adapter.parse(text)
    assert dec.signal == Signal.SELL
    assert dec.confidence == 30.0


def test_rating_structured_overweight(adapter):
    text = "**Rating**: Overweight"
    dec = adapter.parse(text)
    assert dec.signal == Signal.BUY
    assert dec.confidence == 70.0


def test_rating_structured_strong_sell(adapter):
    text = "**Rating**: Strong Sell"
    dec = adapter.parse(text)
    assert dec.signal == Signal.SELL
    assert dec.confidence == 15.0


def test_rating_unknown_fallback(adapter):
    """未识别的 Rating 值 → 兜底解析。"""
    text = "**Rating**: Accumulate"
    dec = adapter.parse(text)
    # "accumulate" 不匹配任何已知信号，fallback HOLD
    assert dec.signal == Signal.HOLD or dec.signal == Signal.BUY  # 宽松断言


# ── 负上下文感知 ─────────────────────────────────────────────────────────────

def test_negative_context_not_buy(adapter):
    """"not recommend BUY" 不应误判为 BUY。"""
    text = "The analyst does not recommend BUY due to valuation concerns."
    dec = adapter.parse(text)
    assert dec.signal != Signal.BUY


def test_negative_context_not_sell(adapter):
    """"not recommend SELL" 不应误判为 SELL。"""
    text = "We do not see a compelling reason to SELL at this time."
    dec = adapter.parse(text)
    assert dec.signal != Signal.SELL


def test_positive_buy_affirmed(adapter):
    """明确推荐 BUY → 正确识别。"""
    text = "We recommend BUY with strong conviction given the growth outlook."
    dec = adapter.parse(text)
    assert dec.signal == Signal.BUY


# ── Keyword fallback（无 Rating 字段）────────────────────────────────────────

def test_keyword_fallback_buy(adapter):
    text = "Momentum is strong and we see upside."
    dec = adapter.parse(text)
    # 无 BUY/SELL/HOLD 关键词 → fallback HOLD
    assert dec.signal == Signal.HOLD


def test_keyword_fallback_with_buy(adapter):
    text = "We are initiating a BUY position on this name."
    dec = adapter.parse(text)
    assert dec.signal == Signal.BUY


def test_keyword_fallback_with_sell(adapter):
    text = "Our base case leads us to SELL this stock."
    dec = adapter.parse(text)
    assert dec.signal == Signal.SELL


# ── Empty / edge cases ───────────────────────────────────────────────────────

def test_empty_text(adapter):
    dec = adapter.parse("")
    assert dec.signal == Signal.HOLD
    assert dec.confidence == 50.0


def test_none_text(adapter):
    dec = adapter.parse(None)  # type: ignore
    assert dec.signal == Signal.HOLD
    assert dec.confidence == 50.0


def test_only_whitespace(adapter):
    dec = adapter.parse("   \n  ")
    assert dec.signal == Signal.HOLD


# ── Thesis 提取 ──────────────────────────────────────────────────────────────

def test_thesis_from_investment_thesis(adapter):
    text = """**Rating**: Buy
**Investment Thesis**: The company has strong moat and pricing power.
**Executive Summary**: Summary here."""
    dec = adapter.parse(text)
    assert "strong moat" in dec.thesis.lower()


def test_thesis_fallback_to_summary(adapter):
    text = """**Rating**: Hold
**Executive Summary**: Mixed signals from analysts.
No thesis section."""
    dec = adapter.parse(text)
    assert "mixed signals" in dec.thesis.lower()


# ── Risks 提取 ───────────────────────────────────────────────────────────────

def test_risks_extraction(adapter):
    text = """**Rating**: Sell
Key risks include:
- Valuation is stretched
- Revenue growth decelerating
- Competitive pressure increasing
Other content here."""
    dec = adapter.parse(text)
    assert len(dec.risks) >= 2
    assert any("valuation" in r.lower() or "估值" in r for r in dec.risks)


def test_no_risks(adapter):
    text = "**Rating**: Buy\nNo risks identified."
    dec = adapter.parse(text)
    # 可能无风险条目
    assert isinstance(dec.risks, list)


# ── Expected return ──────────────────────────────────────────────────────────

def test_expected_return_buy(adapter):
    text = "**Rating**: Buy\nWe expect the stock to gain 15% over the next year."
    dec = adapter.parse(text)
    assert dec.expected_return is not None
    assert 10 <= dec.expected_return <= 20


def test_expected_return_buy_excludes_pe(adapter):
    """PE=19 等财务比率不应被当作预期收益。"""
    text = "**Rating**: Buy\nPE ratio is 19x and PEG is 1.5."
    dec = adapter.parse(text)
    # PE/PEG 行应被排除
    assert dec.expected_return is None or abs(dec.expected_return) < 5


# ── InvestmentDecision dataclass ─────────────────────────────────────────────

def test_investment_decision_to_dict(adapter):
    dec = InvestmentDecision(
        signal=Signal.BUY,
        confidence=85.0,
        expected_return=12.5,
        thesis="Strong fundamentals",
        risks=["val risk", "macro risk"],
    )
    d = dec.to_dict()
    assert d["signal"] == "BUY"
    assert d["confidence"] == 85.0
    assert d["risks"] == ["val risk", "macro risk"]


def test_investment_decision_fallback():
    dec = InvestmentDecision.fallback(Signal.SELL, 30.0)
    assert dec.signal == Signal.SELL
    assert dec.confidence == 30.0
    assert dec.risks == []


# ── 端到端：StockAnalysisResult 集成 ─────────────────────────────────────────

def test_stock_result_has_investment_decision():
    """StockAnalysisResult 应包含 investment_decision 字段。"""
    from trade_krono_cli.ta_runner import StockAnalysisResult

    result = StockAnalysisResult(
        ticker="sh.600519", date="2026-08-11",
        investment_decision=InvestmentDecision(
            signal=Signal.SELL, confidence=40.0,
            thesis="估值偏高", risks=["估值风险"],
        ),
    )
    assert result.investment_decision is not None
    assert result.investment_decision.signal == Signal.SELL
    assert result.confidence is None  # legacy 字段未设置
    # decision 属性应 fallback 到 investment_decision
    assert result.decision.signal == Signal.SELL
    assert result.decision.confidence == 40.0


def test_stock_result_legacy_fallback():
    """无 investment_decision 时，decision 属性使用 legacy 字段。"""
    from trade_krono_cli.ta_runner import StockAnalysisResult

    result = StockAnalysisResult(
        ticker="sh.600519", date="2026-08-11",
        signal="BUY", confidence=80.0, reasoning="good",
    )
    assert result.decision.signal == Signal.BUY
    assert result.decision.confidence == 80.0


def test_stock_result_to_dict_includes_decision():
    from trade_krono_cli.ta_runner import StockAnalysisResult

    result = StockAnalysisResult(
        ticker="sh.600519", date="2026-08-11",
        investment_decision=InvestmentDecision(
            signal=Signal.BUY, confidence=82.0,
            thesis="基本面良好",
        ),
    )
    d = result.to_dict()
    assert "investment_decision" in d
    assert d["investment_decision"]["signal"] == "BUY"
    assert d["investment_decision"]["confidence"] == 82.0


# ── reports_raw：完整报告存储 ─────────────────────────────────────────────────

def test_reports_raw_vs_summary(adapter):
    """reports_raw 应保留完整文本，reports 应为 500 字摘要。"""
    long_text = "x" * 1000  # 1000字符的模拟报告
    result = StockAnalysisResult(
        ticker="sh.600519", date="2026-08-11",
        reports_raw={"market": long_text},
        reports={"market": long_text[:500]},
    )
    assert len(result.reports_raw["market"]) == 1000
    assert len(result.reports["market"]) == 500


def test_save_raw_reports_creates_file(tmp_path):
    """save_raw_reports 应在 raw/{date}/{ticker}.json 写入完整报告。"""
    from trade_krono_cli.ta_runner import TradingAgentsRunner
    from trade_krono_cli.config import reload_settings

    reload_settings()
    runner = TradingAgentsRunner(safe_mode=False)

    result = StockAnalysisResult(
        ticker="sh.600519", date="2026-08-11",
        signal="BUY", confidence=85.0,
        reasoning="这是完整的推理文本，不应该被截断。" * 50,
        reports_raw={
            "market": "完整市场报告内容" * 100,
            "fundamentals": "完整基本面报告内容" * 50,
        },
        reports={"market": "市场报告"[:500], "fundamentals": "基本面"[:500]},
        investment_decision=InvestmentDecision(
            signal=Signal.BUY, confidence=85.0,
            thesis="核心论点", risks=["风险1"],
        ),
    )

    written = runner.save_raw_reports([result], "2026-08-11", results_dir=tmp_path)

    assert "sh.600519" in written
    file_path = written["sh.600519"]
    assert Path(file_path).exists()

    import json
    with open(file_path) as f:
        data = json.load(f)

    assert data["ticker"] == "sh.600519"
    assert data["date"] == "2026-08-11"
    # 完整文本应该保留
    assert len(data["reports_raw"]["market"]) == 800   # "完整市场报告内容" * 100
    assert len(data["reports_raw"]["fundamentals"]) == 450  # "完整基本面报告内容" * 50
    # decision_text 应完整（不截断）
    assert len(data["decision_text"]) > 500
    # investment_decision 应结构化
    assert data["investment_decision"]["signal"] == "BUY"
    assert data["investment_decision"]["confidence"] == 85.0


def test_load_raw_report_exists(tmp_path):
    """load_raw_report 能正确读取磁盘上的文件。"""
    from trade_krono_cli.ta_runner import TradingAgentsRunner
    from trade_krono_cli.config import reload_settings

    reload_settings()
    runner = TradingAgentsRunner(safe_mode=False)

    result = StockAnalysisResult(
        ticker="sz.000001", date="2026-08-11",
        reasoning="测试推理",
        reports_raw={"market": "市场报告"},
    )
    runner.save_raw_reports([result], "2026-08-11", results_dir=tmp_path)

    loaded = runner.load_raw_report("sz.000001", "2026-08-11", results_dir=tmp_path)
    assert loaded is not None
    assert loaded["ticker"] == "sz.000001"
    assert loaded["reports_raw"]["market"] == "市场报告"


def test_load_raw_report_missing(tmp_path):
    """不存在的报告文件返回 None。"""
    from trade_krono_cli.ta_runner import TradingAgentsRunner

    runner = TradingAgentsRunner(safe_mode=False)
    loaded = runner.load_raw_report("sh.999999", "2026-01-01", results_dir=tmp_path)
    assert loaded is None


def test_results_dir_contains_raw_subdir(tmp_path):
    """save_raw_reports 创建的目录结构正确。"""
    from trade_krono_cli.ta_runner import TradingAgentsRunner
    from trade_krono_cli.config import reload_settings

    reload_settings()
    runner = TradingAgentsRunner(safe_mode=False)

    result = StockAnalysisResult(
        ticker="sh.600519", date="2026-08-11",
        reasoning="test",
        reports_raw={"market": "m"},
    )
    runner.save_raw_reports([result], "2026-08-11", results_dir=tmp_path)

    raw_dir = tmp_path / "raw" / "2026-08-11"
    assert raw_dir.exists()
    assert (raw_dir / "sh.600519.json").exists()

