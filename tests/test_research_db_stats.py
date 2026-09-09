"""测试 ResearchDatabase — Stats 和查询方法。"""

from __future__ import annotations

import pytest

from trade_krono_cli.research_db import ResearchDatabase


@pytest.fixture
def research_db(tmp_path):
    """使用临时目录创建独立的 ResearchDatabase 实例。"""
    db = tmp_path / "research.db"
    return ResearchDatabase(db_path=db)


def test_stats_empty(research_db) -> None:
    """测试空数据库的统计信息。"""
    stats = research_db.stats()
    assert "research_jobs" in stats
    assert stats["research_jobs"] == 0
    assert "research_ta_analysis" in stats
    assert stats["research_ta_analysis"] == 0


def test_stats_after_insert(research_db) -> None:
    """测试插入数据后的统计信息。"""
    job_id = research_db.create_job("2026-08-11", ["sh.600519", "sz.000858"])
    research_db.complete_job(job_id, n_success=2, elapsed=10.0)

    stats = research_db.stats()
    assert stats["research_jobs"] == 1
    assert stats["research_ta_analysis"] == 0


def test_query_history_empty(research_db) -> None:
    """测试空数据库的历史查询。"""
    history = research_db.query_history("sh.600519")
    assert history == []


def test_query_history_after_insert(research_db) -> None:
    """测试插入数据后的历史查询。"""
    from trade_krono_cli.ta_decision import InvestmentDecision, Signal
    from trade_krono_cli.ta_runner import StockAnalysisResult

    job_id = research_db.create_job("2026-08-11", ["sh.600519"])

    decision = InvestmentDecision(
        signal=Signal.BUY,
        confidence=85.0,
        thesis="",
        risks=[],
    )
    result = StockAnalysisResult(
        ticker="sh.600519",
        date="2026-08-11",
        signal="BUY",
        confidence=85.0,
        reasoning="分析",
        investment_decision=decision,
        elapsed_sec=1.0,
    )
    research_db.insert_ta(job_id, result)

    # 插入信号（必需字段）
    research_db.insert_signals(job_id, [{
        "ticker": "sh.600519",
        "rank": 1,
        "composite_score": 85.0,
        "ta_signal": "BUY",
        "ta_confidence": 85.0,
    }])

    history = research_db.query_history("sh.600519")
    assert len(history) >= 1
    assert history[0]["ta_signal"] == "BUY"


def test_get_latest_signal_for_ticker_empty(research_db) -> None:
    """测试空数据库的最新信号查询。"""
    signal = research_db.get_latest_signal_for_ticker("sh.600519")
    assert signal is None


def test_stats_counts_all_tables(research_db) -> None:
    """测试所有表的统计。"""
    stats = research_db.stats()
    expected_tables = [
        "research_jobs",
        "research_ta_analysis",
        "research_kronos_forecast",
        "research_signals",
        "research_decisions",
        "research_raw_reports",
        "research_backtest_results",
        "research_strategy_runs",
        "research_evaluation_results",
        "research_signal_history",
        "research_committee_deliberations",
        "research_data_snapshots",
        "research_walkforward_runs",
        "research_experiments",
    ]
    for table in expected_tables:
        assert table in stats
        assert isinstance(stats[table], int)
        assert stats[table] >= 0
