"""
研究数据库 — Committee Deliberations 表读写。
"""

from __future__ import annotations

import json
import time

from trade_krono_cli.research_db.base import ResearchDatabase


class CommitteeMixin(ResearchDatabase):
    """Committee Deliberations 表相关方法。"""

    def insert_committee_deliberation(
        self,
        job_id: str,
        ticker: str,
        date: str,
        bull_case: str,
        bear_case: str,
        recommendation: str,
        recommendation_confidence: float,
        reasoning: str,
        agent_consensus: dict,
    ) -> None:
        """写入委员会审议记录。"""
        consensus_json = json.dumps(agent_consensus, ensure_ascii=False)
        with self._conn as conn:
            conn.execute(
                "INSERT OR REPLACE INTO committee_deliberations "
                "(job_id, ticker, date, bull_case, bear_case, "
                " recommendation, recommendation_confidence, reasoning, agent_consensus, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    job_id,
                    ticker,
                    date,
                    bull_case[:2000],
                    bear_case[:2000],
                    recommendation,
                    recommendation_confidence,
                    reasoning[:2000],
                    consensus_json,
                    time.time(),
                ),
            )
            conn.commit()

    def get_committee_for_ticker(
        self,
        ticker: str,
        limit: int = 5,
    ) -> dict | None:
        """获取某只股票最近一次委员会审议结果。"""
        with self._conn as conn:
            row = conn.execute(
                """
                SELECT ticker, date, bull_case, bear_case,
                       recommendation, recommendation_confidence,
                       reasoning, agent_consensus, created_at
                FROM committee_deliberations
                WHERE ticker = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (ticker,),
            ).fetchone()
        if not row:
            return None
        return {
            "ticker": row[0],
            "date": row[1],
            "bull_case": row[2],
            "bear_case": row[3],
            "recommendation": row[4],
            "recommendation_confidence": row[5],
            "reasoning": row[6],
            "agent_consensus": json.loads(row[7]) if row[7] else {},
            "created_at": row[8],
        }
