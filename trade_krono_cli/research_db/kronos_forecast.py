"""
研究数据库 — Kronos Forecast 表读写。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from trade_krono_cli.research_db.base import ResearchDatabase

if TYPE_CHECKING:
    from trade_krono_cli.kronos_runner import KronosForecastResult


class KronosForecastMixin(ResearchDatabase):
    """Kronos Forecast 表相关方法。"""

    def insert_kronos(
        self,
        job_id: str,
        result: KronosForecastResult,
        version_snapshot: dict | None = None,
    ) -> None:
        """写入 Kronos 预测记录。"""
        uncertainty = (
            json.dumps(result.prediction_uncertainty.to_dict())
            if result.prediction_uncertainty
            else None
        )
        band = json.dumps(result.confidence_band) if result.confidence_band else None
        with self._conn as conn:
            conn.execute(
                "INSERT OR REPLACE INTO kronos_forecast "
                "(job_id, ticker, direction, expected_change, predicted_close, "
                " confidence_band, uncertainty, error, elapsed) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    job_id,
                    result.ticker,
                    result.direction,
                    result.expected_change_pct,
                    result.predicted_close_final,
                    band,
                    uncertainty,
                    result.error,
                    result.elapsed_sec,
                ),
            )
            conn.commit()

    def get_kronos_by_job(self, job_id: str) -> list[dict]:
        with self._conn as conn:
            rows = conn.execute(
                "SELECT ticker, direction, expected_change, predicted_close, error "
                "FROM kronos_forecast WHERE job_id=? ORDER BY ticker",
                (job_id,),
            ).fetchall()
        return [
            {
                "ticker": r[0],
                "direction": r[1],
                "expected_change": r[2],
                "predicted_close": r[3],
                "error": r[4],
            }
            for r in rows
        ]
