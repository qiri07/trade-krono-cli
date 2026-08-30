"""
测试 research_db.snapshots — Data Snapshots 表读写。
"""

from __future__ import annotations

import pytest

from trade_krono_cli.research_db import ResearchDatabase

# ── SnapshotsMixin ────────────────────────────────────────────────────────────


class TestSnapshotsMixin:
    """Data Snapshots 表读写测试。"""

    @pytest.fixture
    def db(self, tmp_path) -> ResearchDatabase:
        """创建临时数据库实例。"""
        db_path = tmp_path / "research.db"
        return ResearchDatabase(db_path=db_path)

    def test_insert_and_get_snapshot(self, db: ResearchDatabase, tmp_path):
        """插入并读取快照。"""
        snapshot_id = "snap_001"
        db.insert_data_snapshot(
            snapshot_id=snapshot_id,
            cut_date="2026-08-12",
            effective_cut="2026-08-11",
            sources=[{"name": "baostock", "records": 100}],
            description="测试快照",
        )
        result = db.get_data_snapshot(snapshot_id)
        assert result is not None
        assert result["snapshot_id"] == snapshot_id
        assert result["cut_date"] == "2026-08-12"
        assert result["effective_cut"] == "2026-08-11"
        assert result["sources"] == [{"name": "baostock", "records": 100}]
        assert result["description"] == "测试快照"
        assert result["created_at"] > 0

    def test_get_missing_snapshot(self, db: ResearchDatabase):
        """不存在的快照应返回 None。"""
        result = db.get_data_snapshot("non_existent")
        assert result is None

    def test_insert_overwrites_existing(self, db: ResearchDatabase):
        """相同 snapshot_id 插入应覆盖旧数据。"""
        snapshot_id = "snap_001"
        db.insert_data_snapshot(
            snapshot_id=snapshot_id,
            cut_date="2026-08-12",
            effective_cut="2026-08-11",
            sources=[{"name": "baostock"}],
            description="初版",
        )
        db.insert_data_snapshot(
            snapshot_id=snapshot_id,
            cut_date="2026-08-13",
            effective_cut="2026-08-12",
            sources=[{"name": "akshare"}],
            description="更新版",
        )
        result = db.get_data_snapshot(snapshot_id)
        assert result is not None
        assert result["cut_date"] == "2026-08-13"
        assert result["description"] == "更新版"

    def test_insert_empty_sources(self, db: ResearchDatabase):
        """空 sources 列表应正常存储。"""
        db.insert_data_snapshot(
            snapshot_id="snap_empty",
            cut_date="2026-08-12",
            effective_cut="2026-08-11",
            sources=[],
        )
        result = db.get_data_snapshot("snap_empty")
        assert result is not None
        assert result["sources"] == []

    def test_insert_with_unicode_description(self, db: ResearchDatabase):
        """中文描述应正确存储和读取。"""
        db.insert_data_snapshot(
            snapshot_id="snap_cn",
            cut_date="2026-08-12",
            effective_cut="2026-08-11",
            sources=[],
            description="这是一段中文描述测试",
        )
        result = db.get_data_snapshot("snap_cn")
        assert result is not None
        assert result["description"] == "这是一段中文描述测试"
