"""研究数据库 — Signals 表读写。"""

from __future__ import annotations

import json

from trade_krono_cli.research_db.base import ResearchDatabase
from trade_krono_cli.research_db.schema import REASONING_TRUNCATE_LEN


class SignalsMixin(ResearchDatabase):
    """Signals 表相关方法。"""

    def insert_signals(
        self,
        job_id: str,
        merged_items: list[dict],
        version_snapshot: dict | None = None,
    ) -> None:
        """写入合并信号记录（含版本信息）。"""
        for item in merged_items:
            pu = item.get("kronos_prediction_uncertainty")
            uncertainty = json.dumps(pu) if pu else None
            ev = item.get("expected_value")
            ranking_score = item.get("ranking_score")
            signal_assessment = json.dumps(item.get("signal_assessment") or {}, ensure_ascii=False)
            with self._conn as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO signals "
                    "(job_id, ticker, rank, composite_score, ranking_score, ta_signal, "
                    " ta_confidence, ta_reasoning, kronos_direction, "
                    " kronos_change, uncertainty, ta_error, kronos_error, "
                    " signal_assessment_json, expected_value, conflict) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        job_id,
                        item["ticker"],
                        item.get("rank"),
                        item.get("composite_score"),
                        ranking_score,
                        item.get("ta_signal"),
                        item.get("ta_confidence"),
                        item.get("ta_reasoning", "")[:REASONING_TRUNCATE_LEN],
                        item.get("kronos_direction"),
                        item.get("kronos_change_pct"),
                        uncertainty,
                        item.get("ta_error"),
                        item.get("kronos_error"),
                        signal_assessment,
                        ev,
                        item.get("conflict", ""),
                    ),
                )
                conn.commit()

    def get_signals_by_job(self, job_id: str) -> list[dict]:
        with self._conn as conn:
            rows = conn.execute(
                "SELECT ticker, rank, composite_score, ranking_score, ta_signal, ta_confidence, "
                "       kronos_direction, kronos_change, ta_error, kronos_error, "
                "       expected_value, conflict "
                "FROM signals WHERE job_id=? ORDER BY rank",
                (job_id,),
            ).fetchall()
        return [
            {
                "ticker": r[0],
                "rank": r[1],
                "composite_score": r[2],
                "ranking_score": r[3],
                "ta_signal": r[4],
                "ta_confidence": r[5],
                "kronos_direction": r[6],
                "kronos_change": r[7],
                "ta_error": r[8],
                "kronos_error": r[9],
                "expected_value": r[10],
                "conflict": r[11],
            }
            for r in rows
        ]
