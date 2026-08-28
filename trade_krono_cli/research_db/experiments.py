"""
研究数据库 — Experiments 表读写。
"""

from __future__ import annotations

import json
import time
from typing import Any

from trade_krono_cli.research_db.base import ResearchDatabase


class ExperimentsMixin(ResearchDatabase):
    """Experiments 表相关方法。"""

    def insert_experiment(
        self,
        experiment_id: str,
        full_id: str,
        experiment_type: str,
        hypothesis: dict,
        description: str = "",
        config: dict | None = None,
        data_snapshot_id: str | None = None,
        run_ids: list[str] | None = None,
        result_summary: dict | None = None,
        passed: bool | None = None,
        notes: str = "",
    ) -> None:
        """写入实验记录。"""
        with self._conn as conn:
            conn.execute(
                "INSERT OR REPLACE INTO experiments "
                "(experiment_id, full_id, experiment_type, hypothesis_json, "
                " description, config_json, data_snapshot_id, run_ids, "
                " result_summary, passed, notes, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    experiment_id,
                    full_id,
                    experiment_type,
                    json.dumps(hypothesis, ensure_ascii=False),
                    description,
                    json.dumps(config or {}, ensure_ascii=False),
                    data_snapshot_id,
                    json.dumps(run_ids or [], ensure_ascii=False),
                    json.dumps(result_summary or {}, ensure_ascii=False),
                    1 if passed is True else (0 if passed is False else None),
                    notes,
                    time.time(),
                ),
            )
            conn.commit()

    def get_experiment(self, experiment_id: str) -> dict | None:
        with self._conn as conn:
            row = conn.execute(
                "SELECT experiment_id, full_id, experiment_type, hypothesis_json, "
                " description, config_json, data_snapshot_id, run_ids, "
                " result_summary, passed, notes, created_at "
                "FROM experiments WHERE experiment_id=?",
                (experiment_id,),
            ).fetchone()
        if not row:
            return None
        return {
            "experiment_id": row[0],
            "full_id": row[1],
            "experiment_type": row[2],
            "hypothesis": json.loads(row[3]) if row[3] else {},
            "description": row[4],
            "config": json.loads(row[5]) if row[5] else {},
            "data_snapshot_id": row[6],
            "run_ids": json.loads(row[7]) if row[7] else [],
            "result_summary": json.loads(row[8]) if row[8] else {},
            "passed": bool(row[9]) if row[9] is not None else None,
            "notes": row[10],
            "created_at": row[11],
        }

    def list_experiments(
        self,
        experiment_type: str | None = None,
        only_passed: bool | None = None,
        limit: int = 50,
    ) -> list[dict]:
        conditions: list[str] = []
        params: list[Any] = []
        if experiment_type:
            conditions.append("experiment_type = ?")
            params.append(experiment_type)
        if only_passed is not None:
            conditions.append("passed = ?")
            params.append(1 if only_passed else 0)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        with self._conn as conn:
            rows = conn.execute(
                f"SELECT experiment_id, full_id, experiment_type, hypothesis_json, "
                f" description, data_snapshot_id, run_ids, result_summary, passed, created_at "
                f"FROM experiments {where} ORDER BY created_at DESC LIMIT ?",
                [*params, limit],
            ).fetchall()
        return [
            {
                "experiment_id": r[0],
                "full_id": r[1],
                "experiment_type": r[2],
                "hypothesis": json.loads(r[3]) if r[3] else {},
                "description": r[4],
                "data_snapshot_id": r[5],
                "run_ids": json.loads(r[6]) if r[6] else [],
                "result_summary": json.loads(r[7]) if r[7] else {},
                "passed": bool(r[8]) if r[8] is not None else None,
                "created_at": r[9],
            }
            for r in rows
        ]
