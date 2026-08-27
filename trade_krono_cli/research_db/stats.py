"""
研究数据库 — Stats、query_history、get_latest_signal_for_ticker。
"""
from __future__ import annotations

import json
import sqlite3

from trade_krono_cli.research_db.base import ResearchDatabase
from trade_krono_cli.research_db.schema import RESEARCH_TABLES, validate_table_name


class StatsMixin(ResearchDatabase):
    """Stats 和查询相关方法。"""

    def stats(self) -> dict:
        """返回各 research 表统计。"""
        with self._conn as conn:
            result = {}
            for table in ("jobs", "ta_analysis", "kronos_forecast",
                          "signals", "decisions", "raw_reports",
                          "backtest_results", "strategy_runs",
                          "evaluation_results", "signal_history",
                          "committee_deliberations",
                          "data_snapshots", "walkforward_runs", "experiments"):
                validated = validate_table_name(table, RESEARCH_TABLES)
                try:
                    count = conn.execute(
                        f"SELECT COUNT(*) FROM {validated}"
                    ).fetchone()[0]
                    result[f"research_{table}"] = count
                except sqlite3.OperationalError:
                    result[f"research_{table}"] = 0
            return result

    def query_history(
        self, ticker: str, limit: int = 20,
    ) -> list[dict]:
        """查询某只股票的历史分析记录（合并 signals + decisions + 版本信息）。"""
        with self._conn as conn:
            rows = conn.execute(
                """
                SELECT j.date, j.run_id, j.data_version, j.config_hash,
                       s.rank, s.composite_score,
                       s.ta_signal, s.ta_confidence,
                       s.kronos_direction, s.kronos_change,
                       d.decision_json
                FROM signals s
                JOIN jobs j ON s.job_id = j.job_id
                LEFT JOIN decisions d ON s.job_id = d.job_id AND s.ticker = d.ticker
                WHERE s.ticker = ?
                ORDER BY j.run_at DESC
                LIMIT ?
                """,
                (ticker, limit),
            ).fetchall()
        return [
            {
                "date": r[0], "run_id": r[1],
                "data_version": r[2], "config_hash": r[3],
                "rank": r[4], "composite_score": r[5],
                "ta_signal": r[6], "ta_confidence": r[7],
                "kronos_direction": r[8], "kronos_change": r[9],
                "decision": json.loads(r[10]) if r[10] else None,
            }
            for r in rows
        ]

    def get_latest_signal_for_ticker(self, ticker: str) -> dict | None:
        """
        获取某只股票在 signal_history 表中的最新生命周期记录。

        Returns
        -------
        dict with keys: ticker, date, signal, confidence, composite_score,
            lifecycle_state, previous_state, transition_reason, job_id, run_id
            or None if no record exists.
        """
        with self._conn as conn:
            row = conn.execute(
                """
                SELECT ticker, date, signal, confidence, composite_score,
                       lifecycle_state, previous_state, transition_reason,
                       job_id, run_id
                FROM signal_history
                WHERE ticker = ?
                ORDER BY date DESC
                LIMIT 1
                """,
                (ticker,),
            ).fetchone()
        if not row:
            return None
        return {
            "ticker": row[0],
            "date": row[1],
            "signal": row[2],
            "confidence": row[3],
            "composite_score": row[4],
            "lifecycle_state": row[5],
            "previous_state": row[6],
            "transition_reason": row[7],
            "job_id": row[8],
            "run_id": row[9],
        }
