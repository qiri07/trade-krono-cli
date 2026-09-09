"""测试 ResearchDatabase — Signals 表 CRUD。"""

from __future__ import annotations

import pytest

from trade_krono_cli.research_db import ResearchDatabase


@pytest.fixture
def research_db(tmp_path):
    """使用临时目录创建独立的 ResearchDatabase 实例。"""
    db = tmp_path / "research.db"
    return ResearchDatabase(db_path=db)


def test_insert_signals(research_db) -> None:
    """测试写入合并信号记录。"""
    job_id = research_db.create_job("2026-08-11", ["sh.600519", "sz.000858"])

    merged_items = [
        {
            "ticker": "sh.600519",
            "rank": 1,
            "composite_score": 85.0,
            "ranking_score": 80.0,
            "ta_signal": "BUY",
            "ta_confidence": 80.0,
            "ta_reasoning": "基本面良好",
            "kronos_direction": "UP",
            "kronos_change_pct": 3.2,
            "ta_error": None,
            "kronos_error": None,
            "signal_assessment": {"bullish_score": 0.8},
            "expected_value": 2.5,
            "conflict": "",
        },
        {
            "ticker": "sz.000858",
            "rank": 2,
            "composite_score": 60.0,
            "ranking_score": 55.0,
            "ta_signal": "HOLD",
            "ta_confidence": 60.0,
            "ta_reasoning": "观望",
            "kronos_direction": "DOWN",
            "kronos_change_pct": -1.5,
            "ta_error": None,
            "kronos_error": None,
            "signal_assessment": None,
            "expected_value": None,
            "conflict": "TA与Kronos信号冲突",
        },
    ]

    research_db.insert_signals(job_id, merged_items)

    signals = research_db.get_signals_by_job(job_id)
    assert len(signals) == 2
    assert signals[0]["ticker"] == "sh.600519"
    assert signals[0]["rank"] == 1
    assert signals[0]["ta_signal"] == "BUY"
    assert signals[1]["ticker"] == "sz.000858"
    assert signals[1]["conflict"] == "TA与Kronos信号冲突"


def test_insert_signals_with_version(research_db) -> None:
    """测试写入带版本快照的信号记录。"""
    job_id = research_db.create_job("2026-08-11", ["sh.600519"])

    merged_items = [
        {
            "ticker": "sh.600519",
            "rank": 1,
            "composite_score": 85.0,
            "ta_signal": "BUY",
            "ta_confidence": 80.0,
            "kronos_direction": "UP",
            "kronos_change_pct": 3.2,
        },
    ]

    version_snapshot = {"pipeline_version": "v2.0", "scoring_strategy": "default"}
    research_db.insert_signals(job_id, merged_items, version_snapshot=version_snapshot)

    signals = research_db.get_signals_by_job(job_id)
    assert len(signals) == 1
    assert signals[0]["ta_signal"] == "BUY"


def test_insert_signals_empty(research_db) -> None:
    """测试写入空信号列表。"""
    job_id = research_db.create_job("2026-08-11", [])
    research_db.insert_signals(job_id, [])
    signals = research_db.get_signals_by_job(job_id)
    assert len(signals) == 0


def test_get_signals_by_job_not_found(research_db) -> None:
    """测试查询不存在的job_id。"""
    signals = research_db.get_signals_by_job("nonexistent")
    assert signals == []


def test_insert_signals_overwrite(research_db) -> None:
    """测试同一ticker的信号会被覆盖。"""
    job_id = research_db.create_job("2026-08-11", ["sh.600519"])

    items1 = [{"ticker": "sh.600519", "rank": 1, "composite_score": 85.0, "ta_signal": "BUY"}]
    items2 = [{"ticker": "sh.600519", "rank": 1, "composite_score": 90.0, "ta_signal": "STRONG_BUY"}]

    research_db.insert_signals(job_id, items1)
    research_db.insert_signals(job_id, items2)

    signals = research_db.get_signals_by_job(job_id)
    assert len(signals) == 1
    assert signals[0]["composite_score"] == 90.0
    assert signals[0]["ta_signal"] == "STRONG_BUY"
