"""测试 ResearchDatabase — TA Analysis 表 CRUD。"""

from __future__ import annotations

import pytest

from trade_krono_cli.research_db import ResearchDatabase


@pytest.fixture
def research_db(tmp_path):
    """使用临时目录创建独立的 ResearchDatabase 实例。"""
    db = tmp_path / "research.db"
    return ResearchDatabase(db_path=db)


def test_insert_ta(research_db) -> None:
    """测试写入TA分析记录。"""
    from trade_krono_cli.ta_decision import InvestmentDecision, Signal
    from trade_krono_cli.ta_runner import StockAnalysisResult

    job_id = research_db.create_job("2026-08-11", ["sh.600519"])

    decision = InvestmentDecision(
        signal=Signal.BUY,
        confidence=85.0,
        thesis="长期持有价值股",
        risks=["估值偏高", "市场波动"],
    )
    result = StockAnalysisResult(
        ticker="sh.600519",
        date="2026-08-11",
        signal="BUY",
        confidence=85.0,
        reasoning="基本面良好，成长性强",
        investment_decision=decision,
        elapsed_sec=3.2,
    )

    research_db.insert_ta(job_id, result)

    ta_records = research_db.get_ta_by_job(job_id)
    assert len(ta_records) == 1
    assert ta_records[0]["ticker"] == "sh.600519"
    assert ta_records[0]["signal"] == "BUY"
    assert ta_records[0]["confidence"] == 85.0


def test_insert_ta_no_decision(research_db) -> None:
    """测试写入无投资决策的TA分析。"""
    from trade_krono_cli.ta_runner import StockAnalysisResult

    job_id = research_db.create_job("2026-08-11", ["sh.600519"])

    result = StockAnalysisResult(
        ticker="sh.600519",
        date="2026-08-11",
        signal="HOLD",
        confidence=60.0,
        reasoning="观望",
        investment_decision=None,
        elapsed_sec=1.5,
    )

    research_db.insert_ta(job_id, result)

    ta_records = research_db.get_ta_by_job(job_id)
    assert len(ta_records) == 1
    assert ta_records[0]["signal"] == "HOLD"
    assert ta_records[0]["thesis"] == "观望"


def test_get_ta_by_job_not_found(research_db) -> None:
    """测试查询不存在的job_id。"""
    ta_records = research_db.get_ta_by_job("nonexistent")
    assert ta_records == []


def test_get_latest_ta_for_ticker(research_db) -> None:
    """测试查询最新TA分析记录。"""
    from trade_krono_cli.ta_decision import InvestmentDecision, Signal
    from trade_krono_cli.ta_runner import StockAnalysisResult

    job_id1 = research_db.create_job("2026-08-10", ["sh.600519"])
    job_id2 = research_db.create_job("2026-08-11", ["sh.600519"])

    decision = InvestmentDecision(
        signal=Signal.BUY,
        confidence=80.0,
        thesis="",
        risks=[],
    )

    for job_id in [job_id1, job_id2]:
        result = StockAnalysisResult(
            ticker="sh.600519",
            date="2026-08-11",
            signal="BUY",
            confidence=80.0,
            reasoning="分析",
            investment_decision=decision,
            elapsed_sec=1.0,
        )
        research_db.insert_ta(job_id, result)

    latest = research_db.get_latest_ta_for_ticker("sh.600519", max_age_days=7)
    assert latest is not None
    assert latest["ticker"] == "sh.600519"
    assert latest["signal"] == "BUY"


def test_get_latest_ta_for_ticker_expired(research_db) -> None:
    """测试查询过期TA分析记录。"""
    from trade_krono_cli.ta_decision import InvestmentDecision, Signal
    from trade_krono_cli.ta_runner import StockAnalysisResult

    # 使用一个非常早的日期
    job_id = research_db.create_job("2020-01-01", ["sh.600519"])

    decision = InvestmentDecision(
        signal=Signal.BUY,
        confidence=80.0,
        thesis="",
        risks=[],
    )
    result = StockAnalysisResult(
        ticker="sh.600519",
        date="2020-01-01",
        signal="BUY",
        confidence=80.0,
        reasoning="分析",
        investment_decision=decision,
        elapsed_sec=1.0,
    )
    research_db.insert_ta(job_id, result)

    # 查询最近1年的数据，应该不会过期
    latest = research_db.get_latest_ta_for_ticker("sh.600519", max_age_days=365)
    assert latest is not None
    assert latest["ticker"] == "sh.600519"


def test_insert_ta_overwrite(research_db) -> None:
    """测试同一ticker的分析会被覆盖。"""
    from trade_krono_cli.ta_decision import InvestmentDecision, Signal
    from trade_krono_cli.ta_runner import StockAnalysisResult

    job_id = research_db.create_job("2026-08-11", ["sh.600519"])

    decision1 = InvestmentDecision(
        signal=Signal.BUY,
        confidence=80.0,
        thesis="",
        risks=[],
    )
    decision2 = InvestmentDecision(
        signal=Signal.OVERWEIGHT,
        confidence=90.0,
        thesis="",
        risks=[],
    )

    result1 = StockAnalysisResult(
        ticker="sh.600519",
        date="2026-08-11",
        signal="BUY",
        confidence=80.0,
        reasoning="初始分析",
        investment_decision=decision1,
        elapsed_sec=1.0,
    )

    result2 = StockAnalysisResult(
        ticker="sh.600519",
        date="2026-08-11",
        signal="OVERWEIGHT",
        confidence=90.0,
        reasoning="更新分析",
        investment_decision=decision2,
        elapsed_sec=1.0,
    )

    research_db.insert_ta(job_id, result1)
    research_db.insert_ta(job_id, result2)

    ta_records = research_db.get_ta_by_job(job_id)
    assert len(ta_records) == 1
    assert ta_records[0]["signal"] == "OVERWEIGHT"
    assert ta_records[0]["confidence"] == 90.0
