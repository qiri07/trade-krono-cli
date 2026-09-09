"""测试 ResearchDatabase — Committee Deliberations 表 CRUD。"""

from __future__ import annotations

import pytest

from trade_krono_cli.research_db import ResearchDatabase


@pytest.fixture
def research_db(tmp_path):
    """使用临时目录创建独立的 ResearchDatabase 实例。"""
    db = tmp_path / "research.db"
    return ResearchDatabase(db_path=db)


def test_insert_committee_deliberation(research_db) -> None:
    """测试写入委员会审议记录。"""
    job_id = research_db.create_job("2026-08-11", ["sh.600519"])

    research_db.insert_committee_deliberation(
        job_id=job_id,
        ticker="sh.600519",
        date="2026-08-11",
        bull_case="基本面良好，行业龙头",
        bear_case="估值偏高，市场波动风险",
        recommendation="BUY",
        recommendation_confidence=85.0,
        reasoning="综合分析结果",
        agent_consensus={"ta": "BUY", "kronos": "UP"},
    )

    result = research_db.get_committee_for_ticker("sh.600519")
    assert result is not None
    assert result["ticker"] == "sh.600519"
    assert result["recommendation"] == "BUY"
    assert result["recommendation_confidence"] == 85.0
    assert result["agent_consensus"] == {"ta": "BUY", "kronos": "UP"}
    assert "bull_case" in result
    assert "bear_case" in result


def test_get_committee_for_ticker_not_found(research_db) -> None:
    """测试查询不存在的股票审议记录。"""
    result = research_db.get_committee_for_ticker("sz.999999")
    assert result is None


def test_insert_committee_multiple_tickers(research_db) -> None:
    """测试写入多只股票的审议记录。"""
    job_id = research_db.create_job("2026-08-11", ["sh.600519", "sz.000858"])

    research_db.insert_committee_deliberation(
        job_id=job_id,
        ticker="sh.600519",
        date="2026-08-11",
        bull_case="白酒龙头",
        bear_case="估值高",
        recommendation="BUY",
        recommendation_confidence=85.0,
        reasoning="买入",
        agent_consensus={"ta": "BUY"},
    )

    research_db.insert_committee_deliberation(
        job_id=job_id,
        ticker="sz.000858",
        date="2026-08-11",
        bull_case="科技成长",
        bear_case="竞争激烈",
        recommendation="HOLD",
        recommendation_confidence=60.0,
        reasoning="持有",
        agent_consensus={"ta": "HOLD"},
    )

    result1 = research_db.get_committee_for_ticker("sh.600519")
    assert result1 is not None
    assert result1["recommendation"] == "BUY"

    result2 = research_db.get_committee_for_ticker("sz.000858")
    assert result2 is not None
    assert result2["recommendation"] == "HOLD"


def test_insert_committee_long_text_truncated(research_db) -> None:
    """测试长文本会被截断。"""
    job_id = research_db.create_job("2026-08-11", ["sh.600519"])

    long_text = "x" * 5000

    research_db.insert_committee_deliberation(
        job_id=job_id,
        ticker="sh.600519",
        date="2026-08-11",
        bull_case=long_text,
        bear_case=long_text,
        recommendation="BUY",
        recommendation_confidence=85.0,
        reasoning=long_text,
        agent_consensus={},
    )

    result = research_db.get_committee_for_ticker("sh.600519")
    assert result is not None
    assert len(result["bull_case"]) <= 2000
    assert len(result["bear_case"]) <= 2000
    assert len(result["reasoning"]) <= 2000
