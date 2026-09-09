"""测试 ResearchDatabase — Decisions 表和 Raw Reports 表 CRUD。"""

from __future__ import annotations

import pytest

from trade_krono_cli.research_db import ResearchDatabase


@pytest.fixture
def research_db(tmp_path):
    """使用临时目录创建独立的 ResearchDatabase 实例。"""
    db = tmp_path / "research.db"
    return ResearchDatabase(db_path=db)


def test_insert_decision(research_db) -> None:
    """测试写入投资决策。"""
    from trade_krono_cli.ta_decision import InvestmentDecision, Signal

    job_id = research_db.create_job("2026-08-11", ["sh.600519"])

    decision = InvestmentDecision(
        signal=Signal.BUY,
        confidence=85.0,
        expected_return=12.5,
        position_size=0.08,
        thesis="基本面良好，成长性强",
        risks=["估值偏高", "市场波动"],
    )

    research_db.insert_decision(job_id, "sh.600519", decision, decision.thesis, decision.risks)

    result = research_db.get_decision(job_id, "sh.600519")
    assert result is not None
    assert result["decision"]["signal"] == "BUY"
    assert result["thesis"] == "基本面良好，成长性强"
    assert result["risks"] == ["估值偏高", "市场波动"]


def test_insert_decision_no_thesis(research_db) -> None:
    """测试写入无thesis的投资决策。"""
    from trade_krono_cli.ta_decision import InvestmentDecision, Signal

    job_id = research_db.create_job("2026-08-11", ["sh.600519"])
    decision = InvestmentDecision(
        signal=Signal.HOLD,
        confidence=60.0,
        thesis="",
        risks=[],
    )

    research_db.insert_decision(job_id, "sh.600519", decision, "", [])

    result = research_db.get_decision(job_id, "sh.600519")
    assert result is not None
    assert result["decision"]["signal"] == "HOLD"
    assert result["risks"] == []


def test_get_decision_not_found(research_db) -> None:
    """测试查询不存在的决策。"""
    job_id = research_db.create_job("2026-08-11", ["sh.600519"])
    result = research_db.get_decision(job_id, "sz.000858")
    assert result is None


def test_index_raw_report(research_db) -> None:
    """测试索引原始报告。"""
    job_id = research_db.create_job("2026-08-11", ["sh.600519"])

    research_db.index_raw_report(
        job_id=job_id,
        ticker="sh.600519",
        file_path="/tmp/report.json",
        report_lengths={"ta": 500, "kronos": 300},
    )

    import sqlite3

    conn = sqlite3.connect(research_db._db_path)
    row = conn.execute(
        "SELECT job_id, ticker, path, reports FROM raw_reports WHERE job_id=? AND ticker=?",
        (job_id, "sh.600519"),
    ).fetchone()
    conn.close()

    assert row is not None
    assert row[0] == job_id
    assert row[1] == "sh.600519"
    assert row[2] == "/tmp/report.json"


def test_insert_decision_overwrite(research_db) -> None:
    """测试同一ticker的决策会被覆盖。"""
    from trade_krono_cli.ta_decision import InvestmentDecision, Signal

    job_id = research_db.create_job("2026-08-11", ["sh.600519"])

    decision1 = InvestmentDecision(
        signal=Signal.BUY,
        confidence=80.0,
        thesis="初始分析",
        risks=[],
    )
    decision2 = InvestmentDecision(
        signal=Signal.OVERWEIGHT,
        confidence=90.0,
        thesis="更新分析",
        risks=["新风险"],
    )

    research_db.insert_decision(job_id, "sh.600519", decision1, decision1.thesis, decision1.risks)
    research_db.insert_decision(job_id, "sh.600519", decision2, decision2.thesis, decision2.risks)

    result = research_db.get_decision(job_id, "sh.600519")
    assert result["decision"]["signal"] == "OVERWEIGHT"
    assert result["thesis"] == "更新分析"
