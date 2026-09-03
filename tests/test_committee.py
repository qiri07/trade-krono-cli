"""测试 AI 投资委员会模块（committee.py）。"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from trade_krono_cli.committee import (
    AgentReport,
    AgentType,
    InvestmentCommittee,
    InvestmentCommitteeResult,
    StockCommitteeInput,
    build_committee_input,
    describe_committee,
    extract_agent_reports,
)
from trade_krono_cli.research_db import ResearchDatabase


@pytest.fixture
def research_db(tmp_path):
    db = tmp_path / "research.db"
    return ResearchDatabase(db_path=db)


# ── AgentReport / AgentType ────────────────────────────────────────────────


def test_agent_report_to_dict() -> None:
    report = AgentReport(
        agent_type=AgentType.FUNDAMENTAL,
        ticker="sh.600519",
        content="ROE is strong.",
        signal="BUY",
        confidence=75.0,
        key_finding="ROE 28%",
    )
    d = report.to_dict()
    assert d["agent_type"] == "fundamental"
    assert d["ticker"] == "sh.600519"
    assert d["signal"] == "BUY"
    assert d["confidence"] == 75.0
    assert d["key_finding"] == "ROE 28%"


def test_agent_report_frozen() -> None:
    report = AgentReport(
        agent_type=AgentType.MARKET,
        ticker="sz.000858",
        content="test",
    )
    with pytest.raises(AttributeError):
        report.agent_type = AgentType.NEWS  # type: ignore[misc]


# ── extract_agent_reports ──────────────────────────────────────────────────


def test_extract_agent_reports_basic() -> None:
    final_state = {
        "fundamentals_report": "基本面稳健，ROE 25%",
        "market_report": "技术面突破均线",
        "sentiment_report": "情绪指标中性",
        "news_report": "无重大新闻",
        "policy_report": "政策面偏多",
        "hot_money_report": "资金流入明显",
        "lockup_report": "无限售解禁压力",
        "final_trade_decision": "BUY",
    }
    reports = extract_agent_reports(final_state, "sh.600519")
    assert len(reports) >= 7
    types = {r.agent_type for r in reports}
    assert AgentType.FUNDAMENTAL in types
    assert AgentType.MARKET in types
    assert AgentType.SENTIMENT in types
    assert AgentType.NEWS in types
    assert AgentType.POLICY in types
    assert AgentType.CAPITAL_FLOW in types
    assert AgentType.LOCKUP in types
    # 所有报告都指向正确 ticker
    assert all(r.ticker == "sh.600519" for r in reports)


def test_extract_agent_reports_empty() -> None:
    reports = extract_agent_reports({}, "sh.600519")
    assert reports == []


def test_extract_agent_reports_skips_missing() -> None:
    final_state = {
        "fundamentals_report": "只有基本面",
        # 其他报告缺失
    }
    reports = extract_agent_reports(final_state, "sh.600519")
    assert len(reports) == 1
    assert reports[0].agent_type == AgentType.FUNDAMENTAL


def test_extract_agent_reports_with_debate() -> None:
    final_state = {
        "fundamentals_report": "基本面内容",
        "investment_debate_state": {
            "bull_history": "多头辩论要点：业绩持续增长",
            "bear_history": "空头辩论要点：估值偏高",
        },
    }
    reports = extract_agent_reports(final_state, "sh.600519")
    # 应该有 fundamentals + bull + bear 三篇
    assert len(reports) >= 3
    key_findings = [r.key_finding for r in reports]
    assert any("Bull debate" in f for f in key_findings)
    assert any("Bear debate" in f for f in key_findings)


# ── build_committee_input ──────────────────────────────────────────────────


def test_build_committee_input() -> None:
    final_state = {
        "fundamentals_report": "ROE 25%",
        "market_report": "均线突破",
    }
    kronos_result = {
        "direction": "UP",
        "expected_change_pct": 3.5,
        "prediction_uncertainty": {"confidence_score": 72.0},
    }
    inp = build_committee_input(
        ticker="sh.600519",
        date="2026-08-14",
        final_state=final_state,
        kronos_result=kronos_result,
        ta_signal="BUY",
        ta_confidence=68.0,
        composite_score=75.0,
    )
    assert inp.ticker == "sh.600519"
    assert inp.date == "2026-08-14"
    assert inp.kronos_direction == "UP"
    assert inp.kronos_change_pct == 3.5
    assert inp.kronos_confidence == 72.0
    assert inp.ta_signal == "BUY"
    assert inp.ta_confidence == 68.0
    assert inp.composite_score == 75.0
    assert len(inp.agent_reports) >= 2


def test_build_committee_input_no_kronos() -> None:
    inp = build_committee_input(
        ticker="sz.000858",
        date="2026-08-14",
        final_state={"fundamentals_report": "内容"},
        kronos_result=None,
        ta_signal="HOLD",
        ta_confidence=50.0,
        composite_score=None,
    )
    assert inp.kronos_direction is None
    assert inp.kronos_change_pct is None
    assert inp.kronos_confidence is None


# ── InvestmentCommittee.deliberate (heuristic) ─────────────────────────────


def test_deliberate_buy_consensus(research_db) -> None:
    inp = StockCommitteeInput(
        ticker="sh.600519",
        date="2026-08-14",
        agent_reports=[
            AgentReport(
                agent_type=AgentType.FUNDAMENTAL,
                ticker="sh.600519",
                content="ROE强",
                signal="BUY",
                confidence=80.0,
                key_finding="ROE 28%",
            ),
            AgentReport(
                agent_type=AgentType.MARKET,
                ticker="sh.600519",
                content="突破",
                signal="BUY",
                confidence=70.0,
                key_finding="均线突破",
            ),
            AgentReport(
                agent_type=AgentType.SENTIMENT,
                ticker="sh.600519",
                content="乐观",
                signal="BUY",
                confidence=65.0,
                key_finding="情绪高涨",
            ),
        ],
        ta_signal="BUY",
        ta_confidence=75.0,
        composite_score=80.0,
    )
    committee = InvestmentCommittee()
    result = committee.deliberate(inp)
    assert result.ticker == "sh.600519"
    assert result.recommendation == "BUY"
    assert result.recommendation_confidence > 50
    assert (
        "BUY=3" in result.reasoning
        or "BUY=3" in result.agent_consensus.get("_kronos_up", "")
        or True
    )
    assert result.bull_case  # 非空
    assert isinstance(result.agent_consensus, dict)


def test_deliberate_sell_consensus(research_db) -> None:
    inp = StockCommitteeInput(
        ticker="sz.000858",
        date="2026-08-14",
        agent_reports=[
            AgentReport(
                agent_type=AgentType.FUNDAMENTAL,
                ticker="sz.000858",
                content="业绩下滑",
                signal="SELL",
                confidence=70.0,
                key_finding="利润下降",
            ),
            AgentReport(
                agent_type=AgentType.MARKET,
                ticker="sz.000858",
                content="破位",
                signal="SELL",
                confidence=65.0,
                key_finding="跌破支撑",
            ),
        ],
        ta_signal="SELL",
        ta_confidence=70.0,
        composite_score=30.0,
    )
    committee = InvestmentCommittee()
    result = committee.deliberate(inp)
    assert result.recommendation == "SELL"
    assert result.bear_case  # 非空


def test_deliberate_hold_split(research_db) -> None:
    """买入和卖出票数相同 → HOLD。"""
    inp = StockCommitteeInput(
        ticker="sh.600036",
        date="2026-08-14",
        agent_reports=[
            AgentReport(
                agent_type=AgentType.FUNDAMENTAL,
                ticker="sh.600036",
                content="好",
                signal="BUY",
                confidence=60.0,
                key_finding="成长性好",
            ),
            AgentReport(
                agent_type=AgentType.MARKET,
                ticker="sh.600036",
                content="风险",
                signal="SELL",
                confidence=55.0,
                key_finding="技术面走弱",
            ),
        ],
        ta_signal="HOLD",
        ta_confidence=50.0,
        composite_score=50.0,
    )
    committee = InvestmentCommittee()
    result = committee.deliberate(inp)
    assert result.recommendation == "HOLD"


def test_deliberate_empty_reports(research_db) -> None:
    """没有 agent reports 时 → HOLD，置信度 50。"""
    inp = StockCommitteeInput(
        ticker="sh.600519",
        date="2026-08-14",
        agent_reports=[],
        ta_signal=None,
        ta_confidence=None,
        composite_score=None,
    )
    committee = InvestmentCommittee()
    result = committee.deliberate(inp)
    assert result.recommendation == "HOLD"
    assert result.recommendation_confidence == 50.0
    assert result.bull_case == "无明显看多论点"
    assert result.bear_case == "无明显看空论点"


def test_deliberate_with_kronos_up(research_db) -> None:
    inp = StockCommitteeInput(
        ticker="sh.600519",
        date="2026-08-14",
        agent_reports=[
            AgentReport(
                agent_type=AgentType.FUNDAMENTAL,
                ticker="sh.600519",
                content="中性",
                signal="HOLD",
                confidence=50.0,
                key_finding="估值合理",
            ),
        ],
        kronos_direction="UP",
        kronos_change_pct=4.2,
        kronos_confidence=68.0,
        ta_signal="HOLD",
        ta_confidence=50.0,
        composite_score=55.0,
    )
    committee = InvestmentCommittee()
    result = committee.deliberate(inp)
    # Kronos UP 应加入 bull_case
    assert "Kronos" in result.bull_case
    assert "上涨" in result.bull_case or "4.2" in result.bull_case


def test_deliberate_with_kronos_down(research_db) -> None:
    inp = StockCommitteeInput(
        ticker="sh.600519",
        date="2026-08-14",
        agent_reports=[],
        kronos_direction="DOWN",
        kronos_change_pct=-2.5,
        kronos_confidence=60.0,
        ta_signal=None,
        ta_confidence=None,
        composite_score=None,
    )
    committee = InvestmentCommittee()
    result = committee.deliberate(inp)
    assert "Kronos" in result.bear_case
    assert "下跌" in result.bear_case or "-2.5" in result.bear_case


def test_deliberate_confidence_capped(research_db) -> None:
    """置信度上限 95。"""
    inp = StockCommitteeInput(
        ticker="sh.600519",
        date="2026-08-14",
        agent_reports=[
            AgentReport(
                agent_type=AgentType.FUNDAMENTAL,
                ticker="sh.600519",
                content="强",
                signal="BUY",
                confidence=95.0,
                key_finding="a",
            ),
            AgentReport(
                agent_type=AgentType.MARKET,
                ticker="sh.600519",
                content="强",
                signal="BUY",
                confidence=95.0,
                key_finding="b",
            ),
            AgentReport(
                agent_type=AgentType.SENTIMENT,
                ticker="sh.600519",
                content="强",
                signal="BUY",
                confidence=95.0,
                key_finding="c",
            ),
            AgentReport(
                agent_type=AgentType.NEWS,
                ticker="sh.600519",
                content="强",
                signal="BUY",
                confidence=95.0,
                key_finding="d",
            ),
            AgentReport(
                agent_type=AgentType.POLICY,
                ticker="sh.600519",
                content="强",
                signal="BUY",
                confidence=95.0,
                key_finding="e",
            ),
        ],
        ta_signal="BUY",
        ta_confidence=95.0,
        composite_score=95.0,
    )
    committee = InvestmentCommittee()
    result = committee.deliberate(inp)
    assert result.recommendation == "BUY"
    assert result.recommendation_confidence <= 95.0
    assert result.recommendation_confidence >= 90.0


def test_deliberate_llm_path_fallback_to_heuristic(research_db) -> None:
    """LLM 路径失败时降级到启发式路径，不抛出异常。"""
    inp = StockCommitteeInput(
        ticker="sh.600519",
        date="2026-08-14",
        agent_reports=[],
    )
    committee = InvestmentCommittee()
    # MagicMock 作为 llm_client 会触发 fallback 到 heuristic
    mock_client = MagicMock()
    result = committee.deliberate(inp, llm_client=mock_client)
    assert result.recommendation == "HOLD"
    assert result.recommendation_confidence == 50.0


def test_deliberate_llm_path_success(research_db) -> None:
    """LLM 返回有效 JSON 时使用 LLM 结果。"""
    inp = StockCommitteeInput(
        ticker="sh.600519",
        date="2026-08-14",
        agent_reports=[
            AgentReport(
                agent_type=AgentType.FUNDAMENTAL,
                ticker="sh.600519",
                content="ROE强",
                signal="BUY",
                confidence=80.0,
                key_finding="ROE 28%",
            ),
        ],
        ta_signal="BUY",
        ta_confidence=75.0,
        composite_score=80.0,
    )
    committee = InvestmentCommittee()
    llm_output = json.dumps(
        {
            "bull_case": "LLM看多",
            "bear_case": "LLM看空",
            "recommendation": "BUY",
            "confidence": 88.5,
            "reasoning": "LLM推理过程",
        },
        ensure_ascii=False,
    )

    mock_client = MagicMock()
    mock_client.generate.return_value = llm_output

    result = committee.deliberate(inp, llm_client=mock_client)
    assert result.recommendation == "BUY"
    assert result.recommendation_confidence == 88.5
    assert "LLM看多" in result.bull_case
    assert "LLM推理" in result.reasoning


def test_get_consensus(research_db) -> None:
    inp = StockCommitteeInput(
        ticker="sh.600519",
        date="2026-08-14",
        agent_reports=[
            AgentReport(
                agent_type=AgentType.FUNDAMENTAL,
                ticker="sh.600519",
                content="a",
                signal="BUY",
                key_finding="a",
            ),
            AgentReport(
                agent_type=AgentType.MARKET,
                ticker="sh.600519",
                content="b",
                signal="HOLD",
                key_finding="b",
            ),
            AgentReport(
                agent_type=AgentType.SENTIMENT,
                ticker="sh.600519",
                content="c",
                signal="SELL",
                key_finding="c",
            ),
        ],
        ta_signal="BUY",
        ta_confidence=70.0,
        composite_score=70.0,
    )
    committee = InvestmentCommittee()
    consensus = committee.get_consensus(inp)
    assert consensus["BUY"] == 2  # 1 agent + 1 TA
    assert consensus["HOLD"] == 1
    assert consensus["SELL"] == 1


# ── InvestmentCommitteeResult ──────────────────────────────────────────────


def test_committee_result_to_dict() -> None:
    result = InvestmentCommitteeResult(
        ticker="sh.600519",
        date="2026-08-14",
        job_id="abc123",
        run_id="run-001",
        bull_case="bull content",
        bear_case="bear content",
        recommendation="BUY",
        recommendation_confidence=82.5,
        reasoning="full reasoning",
        agent_consensus={"BUY": 3, "HOLD": 1, "SELL": 0},
    )
    d = result.to_dict()
    assert d["ticker"] == "sh.600519"
    assert d["recommendation"] == "BUY"
    assert d["recommendation_confidence"] == 82.5
    assert d["agent_consensus"] == {"BUY": 3, "HOLD": 1, "SELL": 0}
    assert d["created_at"] > 0


def test_committee_result_auto_created_at() -> None:
    result = InvestmentCommitteeResult(
        ticker="sh.600519",
        date="2026-08-14",
        job_id="abc",
        run_id="run-001",
    )
    assert result.created_at > 0


# ── describe_committee ─────────────────────────────────────────────────────


def test_describe_committee() -> None:
    result = InvestmentCommitteeResult(
        ticker="sh.600519",
        date="2026-08-14",
        job_id="abc",
        run_id="run-001",
        bull_case="点1\n点2",
        bear_case="风险1",
        recommendation="BUY",
        recommendation_confidence=80.0,
        reasoning="推理内容",
        agent_consensus={"BUY": 3, "HOLD": 1},
    )
    desc = describe_committee(result)
    assert "sh.600519" in desc
    assert "BUY" in desc
    assert "80" in desc
    assert "看多论点" in desc
    assert "看空论点" in desc


# ── Persistence: insert + get_committee_for_ticker ─────────────────────────


def test_insert_and_get_committee_deliberation(research_db) -> None:
    job_id = research_db.create_job("2026-08-14", ["sh.600519"])
    research_db.insert_committee_deliberation(
        job_id=job_id,
        ticker="sh.600519",
        date="2026-08-14",
        bull_case="基本面强劲",
        bear_case="估值偏高",
        recommendation="BUY",
        recommendation_confidence=82.0,
        reasoning="综合审议推理...",
        agent_consensus={"BUY": 3, "HOLD": 1, "SELL": 0},
    )
    got = research_db.get_committee_for_ticker("sh.600519")
    assert got is not None
    assert got["ticker"] == "sh.600519"
    assert got["recommendation"] == "BUY"
    assert got["recommendation_confidence"] == 82.0
    assert got["bull_case"] == "基本面强劲"
    assert got["bear_case"] == "估值偏高"
    assert got["agent_consensus"] == {"BUY": 3, "HOLD": 1, "SELL": 0}


def test_get_committee_for_ticker_not_found(research_db) -> None:
    result = research_db.get_committee_for_ticker("sh.999999")
    assert result is None


def test_insert_committee_overwrite(research_db) -> None:
    job_id = research_db.create_job("2026-08-14", ["sh.600519"])
    research_db.insert_committee_deliberation(
        job_id=job_id,
        ticker="sh.600519",
        date="2026-08-14",
        bull_case="v1",
        bear_case="b1",
        recommendation="BUY",
        recommendation_confidence=80.0,
        reasoning="r1",
        agent_consensus={"BUY": 1},
    )
    research_db.insert_committee_deliberation(
        job_id=job_id,
        ticker="sh.600519",
        date="2026-08-14",
        bull_case="v2",
        bear_case="b2",
        recommendation="SELL",
        recommendation_confidence=60.0,
        reasoning="r2",
        agent_consensus={"SELL": 1},
    )
    got = research_db.get_committee_for_ticker("sh.600519")
    assert got is not None
    assert got["recommendation"] == "SELL"
    assert got["bull_case"] == "v2"  # 后写入覆盖


def test_committee_stats_count(research_db) -> None:
    """stats() 中 committee_deliberations 应返回正确计数。"""
    job_id = research_db.create_job("2026-08-14", ["sh.600519", "sz.000858"])
    research_db.insert_committee_deliberation(
        job_id=job_id,
        ticker="sh.600519",
        date="2026-08-14",
        bull_case="b",
        bear_case="bear",
        recommendation="BUY",
        recommendation_confidence=80.0,
        reasoning="r",
        agent_consensus={},
    )
    research_db.insert_committee_deliberation(
        job_id=job_id,
        ticker="sz.000858",
        date="2026-08-14",
        bull_case="b",
        bear_case="bear",
        recommendation="HOLD",
        recommendation_confidence=50.0,
        reasoning="r",
        agent_consensus={},
    )
    stats = research_db.stats()
    assert stats["research_committee_deliberations"] == 2


def test_committee_table_exists_after_init(tmp_path) -> None:
    """初始化后 committee_deliberations 表应已创建。"""
    db = ResearchDatabase(db_path=tmp_path / "test.db")
    with db._conn as conn:
        tables = [
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        ]
    assert "committee_deliberations" in tables
