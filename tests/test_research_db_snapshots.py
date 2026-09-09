"""测试 ResearchDatabase — Data Snapshots 表 CRUD。"""

from __future__ import annotations

import pytest

from trade_krono_cli.research_db import ResearchDatabase


@pytest.fixture
def research_db(tmp_path):
    """使用临时目录创建独立的 ResearchDatabase 实例。"""
    db = tmp_path / "research.db"
    return ResearchDatabase(db_path=db)


def test_insert_data_snapshot(research_db) -> None:
    """测试写入数据快照。"""
    snapshot_id = "snap_001"
    cut_date = "2026-08-11"
    effective_cut = "2026-08-10"
    sources = [{"name": "akshare", "records": 1000}]
    description = "每日数据快照"

    research_db.insert_data_snapshot(
        snapshot_id=snapshot_id,
        cut_date=cut_date,
        effective_cut=effective_cut,
        sources=sources,
        description=description,
    )

    snapshot = research_db.get_data_snapshot(snapshot_id)
    assert snapshot is not None
    assert snapshot["snapshot_id"] == snapshot_id
    assert snapshot["cut_date"] == cut_date
    assert snapshot["effective_cut"] == effective_cut
    assert snapshot["description"] == description
    assert len(snapshot["sources"]) == 1
    assert snapshot["sources"][0]["name"] == "akshare"


def test_get_data_snapshot_not_found(research_db) -> None:
    """测试查询不存在的快照。"""
    snapshot = research_db.get_data_snapshot("nonexistent")
    assert snapshot is None


def test_insert_data_snapshot_overwrite(research_db) -> None:
    """测试同一快照ID会被覆盖。"""
    snapshot_id = "snap_001"

    research_db.insert_data_snapshot(
        snapshot_id=snapshot_id,
        cut_date="2026-08-10",
        effective_cut="2026-08-09",
        sources=[{"name": "akshare", "records": 500}],
        description="第一次写入",
    )

    research_db.insert_data_snapshot(
        snapshot_id=snapshot_id,
        cut_date="2026-08-11",
        effective_cut="2026-08-10",
        sources=[{"name": "baostock", "records": 1000}],
        description="第二次写入",
    )

    snapshot = research_db.get_data_snapshot(snapshot_id)
    assert snapshot is not None
    assert snapshot["cut_date"] == "2026-08-11"
    assert snapshot["description"] == "第二次写入"
    assert snapshot["sources"][0]["name"] == "baostock"


def test_insert_data_snapshot_empty_sources(research_db) -> None:
    """测试写入空数据源的快照。"""
    snapshot_id = "snap_002"

    research_db.insert_data_snapshot(
        snapshot_id=snapshot_id,
        cut_date="2026-08-11",
        effective_cut="2026-08-10",
        sources=[],
        description="",
    )

    snapshot = research_db.get_data_snapshot(snapshot_id)
    assert snapshot is not None
    assert snapshot["sources"] == []
    assert snapshot["description"] == ""
