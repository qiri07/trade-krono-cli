"""
研究数据库 — Decisions 表和 Raw Reports 表读写。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from trade_krono_cli.research_db.base import ResearchDatabase

if TYPE_CHECKING:
    from trade_krono_cli.ta_decision import InvestmentDecision


class DecisionsMixin(ResearchDatabase):
    """Decisions 表相关方法。"""

    def insert_decision(
        self,
        job_id: str,
        ticker: str,
        decision: InvestmentDecision,
        thesis: str,
        risks: list[str],
    ) -> None:
        decision_json = json.dumps(decision.to_dict(), ensure_ascii=False)
        risks_json = json.dumps(risks, ensure_ascii=False)
        with self._conn as conn:
            conn.execute(
                "INSERT OR REPLACE INTO decisions "
                "(job_id, ticker, decision_json, thesis, risks) "
                "VALUES (?,?,?,?,?)",
                (job_id, ticker, decision_json, thesis, risks_json),
            )
            conn.commit()

    def get_decision(self, job_id: str, ticker: str) -> dict | None:
        with self._conn as conn:
            row = conn.execute(
                "SELECT decision_json, thesis, risks FROM decisions WHERE job_id=? AND ticker=?",
                (job_id, ticker),
            ).fetchone()
        if not row:
            return None
        return {
            "decision": json.loads(row[0]),
            "thesis": row[1],
            "risks": json.loads(row[2]) if row[2] else [],
        }


class ReportsMixin(ResearchDatabase):
    """Raw Reports 表相关方法。"""

    def index_raw_report(
        self,
        job_id: str,
        ticker: str,
        file_path: str,
        report_lengths: dict[str, int],
    ) -> None:
        reports_json = json.dumps(report_lengths, ensure_ascii=False)
        with self._conn as conn:
            conn.execute(
                "INSERT OR REPLACE INTO raw_reports "
                "(job_id, ticker, path, reports) VALUES (?,?,?,?)",
                (job_id, ticker, file_path, reports_json),
            )
            conn.commit()
