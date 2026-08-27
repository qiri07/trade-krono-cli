"""
研究数据库 — Walk-Forward Runs 表读写。
"""
from __future__ import annotations

import json
import time
from typing import Any

from trade_krono_cli.research_db.base import ResearchDatabase


class WalkforwardMixin(ResearchDatabase):
    """Walk-Forward Runs 表相关方法。"""

    def insert_walkforward_run(
        self,
        run_id: str,
        experiment_id: str | None,
        ticker: str,
        config: dict,
        total_windows: int,
        valid_windows: int,
        win_rate: float,
        avg_return: float,
        sharpe_annual: float,
        n_records: int,
        elapsed_sec: float,
        snapshot_id: str | None = None,
    ) -> None:
        """写入 walk-forward 评估结果。"""
        with self._conn as conn:
            conn.execute(
                "INSERT OR REPLACE INTO walkforward_runs "
                "(run_id, experiment_id, ticker, config_json, total_windows, valid_windows, "
                " win_rate, avg_return, sharpe_annual, n_records, elapsed_sec, snapshot_id, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    run_id, experiment_id, ticker,
                    json.dumps(config, ensure_ascii=False),
                    total_windows, valid_windows,
                    win_rate, avg_return, sharpe_annual,
                    n_records, elapsed_sec,
                    snapshot_id, time.time(),
                ),
            )
            conn.commit()

    def get_walkforward_runs(
        self,
        experiment_id: str | None = None,
        ticker: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """查询 walk-forward 运行记录。"""
        conditions: list[str] = []
        params: list[Any] = []
        if experiment_id:
            conditions.append("experiment_id = ?")
            params.append(experiment_id)
        if ticker:
            conditions.append("ticker = ?")
            params.append(ticker)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        with self._conn as conn:
            rows = conn.execute(
                f"SELECT run_id, experiment_id, ticker, total_windows, valid_windows, "
                f" win_rate, avg_return, sharpe_annual, n_records, elapsed_sec, snapshot_id, created_at "
                f"FROM walkforward_runs {where} ORDER BY created_at DESC LIMIT ?",
                [*params, limit],
            ).fetchall()
        return [
            {
                "run_id": r[0], "experiment_id": r[1], "ticker": r[2],
                "total_windows": r[3], "valid_windows": r[4],
                "win_rate": r[5], "avg_return": r[6],
                "sharpe_annual": r[7], "n_records": r[8],
                "elapsed_sec": r[9], "snapshot_id": r[10],
                "created_at": r[11],
            }
            for r in rows
        ]
