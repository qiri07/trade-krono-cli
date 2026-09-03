"""测试 InvestmentDecision 数据类 — 序列化/反序列化/边界。"""

from pathlib import Path

from trade_krono_cli.ta_decision import InvestmentDecision, Signal
from trade_krono_cli.ta_runner import StockAnalysisResult


def test_investment_decision_fallback() -> None:
    dec = InvestmentDecision.fallback(Signal.SELL, 30.0)
    assert dec.signal == Signal.SELL
    assert dec.confidence == 30.0
    assert dec.risks == []


# ── 端到端：StockAnalysisResult 集成 ─────────────────────────────────────────


def test_stock_result_has_investment_decision() -> None:
    """StockAnalysisResult 应包含 investment_decision 字段。"""
    from trade_krono_cli.ta_runner import StockAnalysisResult

    result = StockAnalysisResult(
        ticker="sh.600519",
        date="2026-08-11",
        investment_decision=InvestmentDecision(
            signal=Signal.SELL,
            confidence=40.0,
            thesis="估值偏高",
            risks=["估值风险"],
        ),
    )
    assert result.investment_decision is not None
    assert result.investment_decision.signal == Signal.SELL
    assert result.confidence is None  # legacy 字段未设置
    # decision 属性应 fallback 到 investment_decision
    assert result.decision.signal == Signal.SELL
    assert result.decision.confidence == 40.0


def test_stock_result_legacy_fallback() -> None:
    """无 investment_decision 时，decision 属性使用 legacy 字段。"""
    from trade_krono_cli.ta_runner import StockAnalysisResult

    result = StockAnalysisResult(
        ticker="sh.600519",
        date="2026-08-11",
        signal="BUY",
        confidence=80.0,
        reasoning="good",
    )
    assert result.decision.signal == Signal.BUY
    assert result.decision.confidence == 80.0


def test_stock_result_to_dict_includes_decision() -> None:
    from trade_krono_cli.ta_runner import StockAnalysisResult

    result = StockAnalysisResult(
        ticker="sh.600519",
        date="2026-08-11",
        investment_decision=InvestmentDecision(
            signal=Signal.BUY,
            confidence=82.0,
            thesis="基本面良好",
        ),
    )
    d = result.to_dict()
    assert "investment_decision" in d
    assert d["investment_decision"]["signal"] == "BUY"
    assert d["investment_decision"]["confidence"] == 82.0


# ── reports_raw：完整报告存储 ─────────────────────────────────────────────────


def test_save_raw_reports_creates_file(tmp_path) -> None:
    """save_raw_reports 应在 raw/{date}/{ticker}.json 写入完整报告。"""
    from trade_krono_cli.config import reload_settings
    from trade_krono_cli.ta_runner import TradingAgentsRunner

    reload_settings()
    runner = TradingAgentsRunner(safe_mode=False)

    result = StockAnalysisResult(
        ticker="sh.600519",
        date="2026-08-11",
        signal="BUY",
        confidence=85.0,
        reasoning="这是完整的推理文本，不应该被截断。" * 50,
        reports_raw={
            "market": "完整市场报告内容" * 100,
            "fundamentals": "完整基本面报告内容" * 50,
        },
        reports={"market": "市场报告"[:500], "fundamentals": "基本面"[:500]},
        investment_decision=InvestmentDecision(
            signal=Signal.BUY,
            confidence=85.0,
            thesis="核心论点",
            risks=["风险1"],
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
    assert len(data["reports_raw"]["market"]) == 800  # "完整市场报告内容" * 100
    assert len(data["reports_raw"]["fundamentals"]) == 450  # "完整基本面报告内容" * 50
    # decision_text 应完整（不截断）
    assert len(data["decision_text"]) > 500
    # investment_decision 应结构化
    assert data["investment_decision"]["signal"] == "BUY"
    assert data["investment_decision"]["confidence"] == 85.0


def test_load_raw_report_exists(tmp_path) -> None:
    """load_raw_report 能正确读取磁盘上的文件。"""
    from trade_krono_cli.config import reload_settings
    from trade_krono_cli.ta_runner import TradingAgentsRunner

    reload_settings()
    runner = TradingAgentsRunner(safe_mode=False)

    result = StockAnalysisResult(
        ticker="sz.000001",
        date="2026-08-11",
        reasoning="测试推理",
        reports_raw={"market": "市场报告"},
    )
    runner.save_raw_reports([result], "2026-08-11", results_dir=tmp_path)

    loaded = runner.load_raw_report("sz.000001", "2026-08-11", results_dir=tmp_path)
    assert loaded is not None
    assert loaded["ticker"] == "sz.000001"
    assert loaded["reports_raw"]["market"] == "市场报告"


def test_load_raw_report_missing(tmp_path) -> None:
    """不存在的报告文件返回 None。"""
    from trade_krono_cli.ta_runner import TradingAgentsRunner

    runner = TradingAgentsRunner(safe_mode=False)
    loaded = runner.load_raw_report("sh.999999", "2026-01-01", results_dir=tmp_path)
    assert loaded is None


def test_results_dir_contains_raw_subdir(tmp_path) -> None:
    """save_raw_reports 创建的目录结构正确。"""
    from trade_krono_cli.config import reload_settings
    from trade_krono_cli.ta_runner import TradingAgentsRunner

    reload_settings()
    runner = TradingAgentsRunner(safe_mode=False)

    result = StockAnalysisResult(
        ticker="sh.600519",
        date="2026-08-11",
        reasoning="test",
        reports_raw={"market": "m"},
    )
    runner.save_raw_reports([result], "2026-08-11", results_dir=tmp_path)

    raw_dir = tmp_path / "raw" / "2026-08-11"
    assert raw_dir.exists()
    assert (raw_dir / "sh.600519.json").exists()


# ── JSON 结构化解析 ───────────────────────────────────────────────────────────
