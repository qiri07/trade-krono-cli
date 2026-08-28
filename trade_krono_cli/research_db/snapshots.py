"""
研究数据库 — Data Snapshots 表读写。
"""

from __future__ import annotations

import json
import time

from trade_krono_cli.research_db.base import ResearchDatabase


class SnapshotsMixin(ResearchDatabase):
    """Data Snapshots 表相关方法。"""

    def insert_data_snapshot(
        self,
        snapshot_id: str,
        cut_date: str,
        effective_cut: str,
        sources: list[dict],
        description: str = "",
    ) -> None:
        """写入 Point-in-Time 数据快照。"""
        with self._conn as conn:
            conn.execute(
                "INSERT OR REPLACE INTO data_snapshots "
                "(snapshot_id, cut_date, effective_cut, sources, description, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (
                    snapshot_id,
                    cut_date,
                    effective_cut,
                    json.dumps(sources, ensure_ascii=False),
                    description,
                    time.time(),
                ),
            )
            conn.commit()

    def get_data_snapshot(self, snapshot_id: str) -> dict | None:
        with self._conn as conn:
            row = conn.execute(
                "SELECT snapshot_id, cut_date, effective_cut, sources, description, created_at "
                "FROM data_snapshots WHERE snapshot_id=?",
                (snapshot_id,),
            ).fetchone()
        if not row:
            return None
        return {
            "snapshot_id": row[0],
            "cut_date": row[1],
            "effective_cut": row[2],
            "sources": json.loads(row[3]) if row[3] else [],
            "description": row[4],
            "created_at": row[5],
        }
