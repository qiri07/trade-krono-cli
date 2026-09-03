"""研究数据库 — Jobs 表 CRUD。"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING
from uuid import uuid4

from loguru import logger

from trade_krono_cli.research_db.base import ResearchDatabase
from trade_krono_cli.version import build_run_snapshot

if TYPE_CHECKING:
    from trade_krono_cli.config import Settings


class JobMixin(ResearchDatabase):
    """Jobs 表相关方法。"""

    def create_job(
        self,
        date: str,
        tickers: list[str],
        settings: Settings | None = None,
        notes: str | None = None,
    ) -> str:
        """创建新分析作业，返回 job_id。

        Parameters
        ----------
        settings : Settings 对象（可选）
            传入后自动填充 run_id / data_version / model_versions /
            prompt_version / strategy_version / config_hash

        """
        job_id = str(uuid4())[:12]
        run_at = time.time()

        # 版本快照
        snapshot: dict = {}
        if settings is not None:
            snapshot = build_run_snapshot(date, settings)

        with self._conn as conn:
            conn.execute(
                "INSERT INTO jobs "
                "(job_id, run_id, run_at, date, tickers, n_tickers, "
                " n_success, elapsed, data_version, model_versions, "
                " prompt_version, strategy_version, config_hash, "
                " external_repos, notes) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    job_id,
                    snapshot.get("run_id"),
                    run_at,
                    date,
                    json.dumps(tickers, ensure_ascii=False),
                    len(tickers),
                    0,
                    0.0,
                    snapshot.get("data_version"),
                    json.dumps(snapshot.get("model_versions", {}), ensure_ascii=False),
                    snapshot.get("prompt_version"),
                    snapshot.get("strategy_version"),
                    snapshot.get("config_hash"),
                    json.dumps(snapshot.get("external_repos", {}), ensure_ascii=False),
                    notes,
                ),
            )
            conn.commit()

        logger.info(
            f"📋 研究作业创建: job={job_id} run_id={snapshot.get('run_id')} "
            f"date={date} n={len(tickers)}",
        )
        return job_id

    def complete_job(
        self,
        job_id: str,
        n_success: int,
        elapsed: float,
    ) -> None:
        """标记作业完成，更新成功数和耗时。"""
        with self._conn as conn:
            conn.execute(
                "UPDATE jobs SET n_success=?, elapsed=? WHERE job_id=?",
                (n_success, elapsed, job_id),
            )
            conn.commit()

    def get_job(self, job_id: str) -> dict | None:
        """获取作业详情，包含版本快照信息。"""
        with self._conn as conn:
            row = conn.execute(
                "SELECT job_id, run_id, run_at, date, tickers, n_tickers, "
                " n_success, elapsed, data_version, model_versions, "
                " prompt_version, strategy_version, config_hash, "
                " external_repos, notes "
                "FROM jobs WHERE job_id=?",
                (job_id,),
            ).fetchone()
        if not row:
            return None
        return {
            "job_id": row[0],
            "run_id": row[1],
            "run_at": row[2],
            "date": row[3],
            "tickers": json.loads(row[4]),
            "n_tickers": row[5],
            "n_success": row[6],
            "elapsed": row[7],
            "data_version": row[8],
            "model_versions": json.loads(row[9]) if row[9] else {},
            "prompt_version": row[10],
            "strategy_version": row[11],
            "config_hash": row[12],
            "external_repos": json.loads(row[13]) if len(row) > 13 and row[13] else {},
            "notes": row[14] if len(row) > 14 else None,
        }

    def list_jobs(self, limit: int = 20) -> list[dict]:
        """列出最近作业，含版本摘要。"""
        with self._conn as conn:
            rows = conn.execute(
                "SELECT job_id, run_id, date, n_tickers, n_success, elapsed, "
                " data_version, strategy_version, config_hash "
                "FROM jobs ORDER BY run_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {
                "job_id": r[0],
                "run_id": r[1],
                "date": r[2],
                "n_tickers": r[3],
                "n_success": r[4],
                "elapsed": r[5],
                "data_version": r[6],
                "strategy_version": r[7],
                "config_hash": r[8],
            }
            for r in rows
        ]

    def get_run_snapshot(self, job_id: str) -> dict | None:
        """获取某次运行的完整版本快照。"""
        job = self.get_job(job_id)
        if not job:
            return None
        return {
            "run_id": job["run_id"],
            "data_version": job["data_version"],
            "model_versions": job["model_versions"],
            "prompt_version": job["prompt_version"],
            "strategy_version": job["strategy_version"],
            "config_hash": job["config_hash"],
        }
