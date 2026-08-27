"""
研究数据库 — TA Analysis 表读写。
"""
from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

from trade_krono_cli.research_db.base import ResearchDatabase
from trade_krono_cli.research_db.schema import REASONING_TRUNCATE_LEN

if TYPE_CHECKING:
    from trade_krono_cli.ta_runner import StockAnalysisResult


class TaAnalysisMixin(ResearchDatabase):
    """TA Analysis 表相关方法。"""

    def insert_ta(
        self, job_id: str, result: StockAnalysisResult,
        version_snapshot: dict | None = None,
    ) -> None:
        """写入 TA 分析记录（含版本信息）。"""
        risks = (
            json.dumps(result.investment_decision.risks, ensure_ascii=False)
            if result.investment_decision else None
        )
        thesis = (
            result.investment_decision.thesis
            if result.investment_decision else (result.reasoning or "")[:REASONING_TRUNCATE_LEN]
        )
        with self._conn as conn:
            conn.execute(
                "INSERT OR REPLACE INTO ta_analysis "
                "(job_id, ticker, signal, confidence, thesis, risks, error, elapsed) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    job_id, result.ticker,
                    result.signal, result.confidence,
                    thesis, risks,
                    result.error, result.elapsed_sec,
                ),
            )
            conn.commit()

    def get_ta_by_job(self, job_id: str) -> list[dict]:
        with self._conn as conn:
            rows = conn.execute(
                "SELECT ticker, signal, confidence, thesis, risks, error, elapsed "
                "FROM ta_analysis WHERE job_id=? ORDER BY ticker",
                (job_id,),
            ).fetchall()
        return [
            {"ticker": r[0], "signal": r[1], "confidence": r[2],
             "thesis": r[3], "risks": r[4], "error": r[5], "elapsed": r[6]}
            for r in rows
        ]

    def get_latest_ta_for_ticker(
        self, ticker: str, max_age_days: int = 7,
    ) -> dict | None:
        """
        查询最近一次成功的 TA 分析结果（不限定 job_id）。

        Parameters
        ----------
        ticker      : 股票代码
        max_age_days : 最大年龄（天），超过则视为过期

        Returns
        -------
        dict with keys: ticker, signal, confidence, thesis, risks, date, job_id
        or None if no suitable record found.
        """
        cutoff = time.time() - max_age_days * 86400
        with self._conn as conn:
            row = conn.execute(
                """
                SELECT ta.ticker, ta.signal, ta.confidence, ta.thesis, ta.risks,
                       j.date, j.job_id
                FROM ta_analysis ta
                JOIN jobs j ON ta.job_id = j.job_id
                WHERE ta.ticker = ?
                  AND ta.error IS NULL
                  AND j.run_at >= ?
                ORDER BY j.run_at DESC
                LIMIT 1
                """,
                (ticker, cutoff),
            ).fetchone()
        if not row:
            return None
        return {
            "ticker": row[0],
            "signal": row[1],
            "confidence": row[2],
            "thesis": row[3],
            "risks": row[4],
            "date": row[5],
            "job_id": row[6],
        }
