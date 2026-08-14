"""
研究数据库 — 投研数据持久化层。

与 Cache 的区别：
  Cache    — TTL 驱动，过期即失效，纯粹的性能优化
  Research — 永久存储，记录每次分析作业的完整轨迹，支持历史回溯

表结构：
  jobs             每次运行的元数据（唯一 job_id）
  ta_analysis      TA 分析的结构化摘要（按 job_id + ticker）
  kronos_forecast  Kronos 预测的结构化摘要（按 job_id + ticker）
  signals          合并后的综合信号（最终排名，按 job_id + ticker）
  decisions        结构化 InvestmentDecision 完整记录
  raw_reports      原始报告文件的索引（路径映射）
  backtest_results （预留）
  strategy_runs    （预留）
  evaluation_results（预留）

每笔记录携带完整版本快照（data_version / model_version / config_hash / run_id），
支持历史结果复现和回测对比。
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from io import BytesIO
from pathlib import Path
from typing import Optional, TYPE_CHECKING
from uuid import uuid4

import pandas as pd

from loguru import logger
from trade_krono_cli.config import get_settings, Settings
from trade_krono_cli.version import build_run_snapshot

if TYPE_CHECKING:
    from trade_krono_cli.ta_runner import StockAnalysisResult
    from trade_krono_cli.kronos_runner import KronosForecastResult
    from trade_krono_cli.ta_decision import InvestmentDecision

# Truncation length for thesis stored in research db
REASONING_TRUNCATE_LEN = 500

# Whitelist of allowed research table names — prevents SQL injection via f-strings
_RESEARCH_TABLES: frozenset[str] = frozenset({
    "jobs", "ta_analysis", "kronos_forecast",
    "signals", "decisions", "raw_reports",
    "backtest_results", "strategy_runs",
    "evaluation_results", "signal_history", "committee_deliberations",
    "data_snapshots", "walkforward_runs", "experiments",
})


def _validate_table_name(table: str, allowed: frozenset[str]) -> str:
    """Validate a table name against an allowed set. Raises ValueError if invalid."""
    if table not in allowed:
        raise ValueError(f"Unauthorized table: {table}")
    return table


class ResearchDatabase:
    """
    投研数据持久化层。

    表结构见模块文档。
    """

    # 版本追踪列（jobs 表）
    _VERSION_COLS: tuple[str, ...] = (
        "run_id", "data_version", "model_versions",
        "prompt_version", "strategy_version", "config_hash",
        "external_repos",
    )

    def __init__(self, db_path: Optional[Path] = None, settings: Optional[Settings] = None):
        self._db_path = db_path or (
            (settings or get_settings()).cache_dir / "pipeline_cache.db"
        )
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._migrate_schema()
        self._write_lock = threading.Lock()

    @property
    def _conn(self) -> sqlite3.Connection:
        """持久连接（懒初始化），避免每次读写新建连接。"""
        conn = sqlite3.connect(self._db_path, check_same_thread=True)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        """创建所有表（幂等，IF NOT EXISTS）。"""
        with self._conn as conn:
            conn.executescript("""
                -- 每次运行作业（含版本快照）
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id         TEXT PRIMARY KEY,
                    run_id         TEXT,
                    run_at         REAL NOT NULL,
                    date           TEXT NOT NULL,
                    tickers        TEXT NOT NULL,
                    n_tickers      INTEGER NOT NULL,
                    n_success      INTEGER NOT NULL,
                    elapsed        REAL NOT NULL,
                    data_version   TEXT,
                    model_versions TEXT,
                    prompt_version TEXT,
                    strategy_version TEXT,
                    config_hash    TEXT,
                    external_repos TEXT,
                    notes          TEXT
                );

                -- TA 分析结构化摘要
                CREATE TABLE IF NOT EXISTS ta_analysis (
                    job_id         TEXT NOT NULL,
                    ticker         TEXT NOT NULL,
                    signal         TEXT,
                    confidence     REAL,
                    thesis         TEXT,
                    risks          TEXT,
                    error          TEXT,
                    elapsed        REAL,
                    PRIMARY KEY (job_id, ticker),
                    FOREIGN KEY (job_id) REFERENCES jobs(job_id)
                );

                -- Kronos 预测结构化摘要
                CREATE TABLE IF NOT EXISTS kronos_forecast (
                    job_id          TEXT NOT NULL,
                    ticker          TEXT NOT NULL,
                    direction       TEXT,
                    expected_change REAL,
                    predicted_close REAL,
                    confidence_band TEXT,
                    uncertainty     TEXT,
                    error           TEXT,
                    elapsed         REAL,
                    PRIMARY KEY (job_id, ticker),
                    FOREIGN KEY (job_id) REFERENCES jobs(job_id)
                );

                -- 合并后的综合信号
                CREATE TABLE IF NOT EXISTS signals (
                    job_id            TEXT NOT NULL,
                    ticker            TEXT NOT NULL,
                    rank              INTEGER,
                    composite_score   REAL,
                    ta_signal         TEXT,
                    ta_confidence     REAL,
                    ta_reasoning      TEXT,
                    kronos_direction  TEXT,
                    kronos_change     REAL,
                    uncertainty       TEXT,
                    ta_error          TEXT,
                    kronos_error      TEXT,
                    PRIMARY KEY (job_id, ticker),
                    FOREIGN KEY (job_id) REFERENCES jobs(job_id)
                );

                -- 结构化 InvestmentDecision
                CREATE TABLE IF NOT EXISTS decisions (
                    job_id        TEXT NOT NULL,
                    ticker        TEXT NOT NULL,
                    decision_json TEXT NOT NULL,
                    thesis        TEXT,
                    risks         TEXT,
                    PRIMARY KEY (job_id, ticker),
                    FOREIGN KEY (job_id) REFERENCES jobs(job_id)
                );

                -- 原始报告文件索引
                CREATE TABLE IF NOT EXISTS raw_reports (
                    job_id   TEXT NOT NULL,
                    ticker   TEXT NOT NULL,
                    path     TEXT NOT NULL,
                    reports  TEXT,
                    PRIMARY KEY (job_id, ticker),
                    FOREIGN KEY (job_id) REFERENCES jobs(job_id)
                );

                -- 预留表
                CREATE TABLE IF NOT EXISTS backtest_results (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id         TEXT NOT NULL,
                    strategy       TEXT NOT NULL,
                    symbols        TEXT,
                    start_date     TEXT,
                    end_date       TEXT,
                    results        TEXT,
                    created_at     REAL NOT NULL,
                    scoring_strategy TEXT
                );

                CREATE TABLE IF NOT EXISTS strategy_runs (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_at         REAL NOT NULL,
                    strategy       TEXT NOT NULL,
                    params         TEXT,
                    tickers        TEXT,
                    results        TEXT,
                    notes          TEXT,
                    config_hash    TEXT
                );

                -- 信号生命周期表（同一 ticker 跨多次运行的状态演变）
                CREATE TABLE IF NOT EXISTS signal_history (
                    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker              TEXT NOT NULL,
                    date                TEXT NOT NULL,
                    signal              TEXT NOT NULL,
                    confidence          REAL NOT NULL,
                    composite_score     REAL NOT NULL,
                    lifecycle_state     TEXT NOT NULL,
                    previous_state      TEXT,
                    transition_reason   TEXT,
                    job_id              TEXT,
                    run_id              TEXT,
                    thesis_snapshot     TEXT,
                    created_at          REAL NOT NULL,
                    FOREIGN KEY (job_id) REFERENCES jobs(job_id)
                );

                -- 委员会审议记录（Investment Committee  deliberation results）
                CREATE TABLE IF NOT EXISTS committee_deliberations (
                    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id                    TEXT NOT NULL,
                    ticker                    TEXT NOT NULL,
                    date                      TEXT NOT NULL,
                    bull_case                 TEXT,
                    bear_case                 TEXT,
                    recommendation            TEXT,
                    recommendation_confidence REAL,
                    reasoning                 TEXT,
                    agent_consensus           TEXT,
                    created_at                REAL NOT NULL,
                    FOREIGN KEY (job_id) REFERENCES jobs(job_id),
                    UNIQUE (job_id, ticker)
                );

                -- Point-in-Time 数据快照
                CREATE TABLE IF NOT EXISTS data_snapshots (
                    snapshot_id   TEXT PRIMARY KEY,
                    cut_date      TEXT NOT NULL,
                    effective_cut TEXT NOT NULL,
                    sources       TEXT NOT NULL,
                    description   TEXT,
                    created_at    REAL NOT NULL
                );

                -- Walk-Forward 评估结果
                CREATE TABLE IF NOT EXISTS walkforward_runs (
                    run_id         TEXT PRIMARY KEY,
                    experiment_id  TEXT,
                    ticker         TEXT NOT NULL,
                    config_json    TEXT NOT NULL,
                    total_windows  INTEGER,
                    valid_windows  INTEGER,
                    win_rate       REAL,
                    avg_return     REAL,
                    sharpe_annual  REAL,
                    n_records      INTEGER,
                    elapsed_sec    REAL,
                    snapshot_id    TEXT,
                    created_at     REAL NOT NULL,
                    FOREIGN KEY (experiment_id) REFERENCES experiments(experiment_id),
                    FOREIGN KEY (snapshot_id)   REFERENCES data_snapshots(snapshot_id)
                );

                -- 实验注册表
                CREATE TABLE IF NOT EXISTS experiments (
                    experiment_id   TEXT PRIMARY KEY,
                    full_id         TEXT NOT NULL,
                    experiment_type TEXT NOT NULL,
                    hypothesis_json TEXT NOT NULL,
                    description     TEXT,
                    config_json     TEXT,
                    data_snapshot_id TEXT,
                    run_ids         TEXT,
                    result_summary  TEXT,
                    passed          INTEGER,
                    notes           TEXT,
                    created_at      REAL NOT NULL
                );
            """)

    def _migrate_schema(self) -> None:
        """
        向后兼容：为已有表动态添加新版本列。
        不破坏任何现有数据。
        """
        with self._conn as conn:
            info = conn.execute(
                "PRAGMA table_info(jobs)"
            ).fetchall()
            existing_cols = {row[1] for row in info}

            # 批量迁移旧版本缺失的列（run_id 不存在时全部加）
            if "run_id" not in existing_cols:
                for col in self._VERSION_COLS:
                    try:
                        conn.execute(
                            f"ALTER TABLE jobs ADD COLUMN {col} TEXT"
                        )
                        logger.debug(f"📐 Schema 迁移: jobs.{col}")
                    except sqlite3.OperationalError:
                        pass  # 列已存在

            # 增量迁移：逐个检查新版本列是否存在
            for col in ("external_repos",):
                if col not in existing_cols:
                    try:
                        conn.execute(
                            f"ALTER TABLE jobs ADD COLUMN {col} TEXT"
                        )
                        logger.debug(f"📐 Schema 迁移: jobs.{col}")
                    except sqlite3.OperationalError:
                        pass

            # 迁移 backtest_results.scoring_strategy 列
            bt_cols = {row[1] for row in conn.execute(
                "PRAGMA table_info(backtest_results)"
            ).fetchall()}
            if "scoring_strategy" not in bt_cols:
                try:
                    conn.execute(
                        "ALTER TABLE backtest_results ADD COLUMN scoring_strategy TEXT"
                    )
                    logger.debug("📐 Schema 迁移: backtest_results.scoring_strategy")
                except sqlite3.OperationalError:
                    pass

            # 迁移 strategy_runs.config_hash 列
            sr_cols = {row[1] for row in conn.execute(
                "PRAGMA table_info(strategy_runs)"
            ).fetchall()}
            if "config_hash" not in sr_cols:
                try:
                    conn.execute(
                        "ALTER TABLE strategy_runs ADD COLUMN config_hash TEXT"
                    )
                    logger.debug("📐 Schema 迁移: strategy_runs.config_hash")
                except sqlite3.OperationalError:
                    pass

            # 迁移 signals 表新增领域字段
            sig_cols = {row[1] for row in conn.execute(
                "PRAGMA table_info(signals)"
            ).fetchall()}
            for col in ("signal_assessment_json", "expected_value", "conflict"):
                if col not in sig_cols:
                    try:
                        conn.execute(f"ALTER TABLE signals ADD COLUMN {col} TEXT")
                        logger.debug(f"📐 Schema 迁移: signals.{col}")
                    except sqlite3.OperationalError:
                        pass
            # 迁移：ranking_score 列（若 composite_score 已存在但 ranking_score 缺失）
            if "ranking_score" not in sig_cols and "composite_score" in sig_cols:
                try:
                    conn.execute(
                        "ALTER TABLE signals ADD COLUMN ranking_score REAL"
                    )
                    # 将 composite_score 复制到 ranking_score
                    conn.execute(
                        "UPDATE signals SET ranking_score = composite_score "
                        "WHERE ranking_score IS NULL"
                    )
                    logger.debug("📐 Schema 迁移: signals.ranking_score")
                except sqlite3.OperationalError:
                    pass

            # 确保其他表存在
            for table in ("ta_analysis", "kronos_forecast", "signals",
                          "decisions", "raw_reports",
                          "backtest_results", "strategy_runs"):
                validated = _validate_table_name(table, _RESEARCH_TABLES)
                try:
                    conn.execute(f"SELECT 1 FROM {validated} LIMIT 0")
                except sqlite3.OperationalError:
                    pass  # CREATE TABLE IF NOT EXISTS 已在 _init_db 中处理

            conn.commit()

    # ── Jobs ──────────────────────────────────────────

    def create_job(
        self, date: str, tickers: list[str],
        settings=None,
        notes: Optional[str] = None,
    ) -> str:
        """
        创建新分析作业，返回 job_id。

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
                    run_at, date,
                    json.dumps(tickers, ensure_ascii=False),
                    len(tickers), 0, 0.0,
                    snapshot.get("data_version"),
                    json.dumps(snapshot.get("model_versions", {}),
                               ensure_ascii=False),
                    snapshot.get("prompt_version"),
                    snapshot.get("strategy_version"),
                    snapshot.get("config_hash"),
                    json.dumps(snapshot.get("external_repos", {}),
                               ensure_ascii=False),
                    notes,
                ),
            )
            conn.commit()

        logger.info(
            f"📋 研究作业创建: job={job_id} run_id={snapshot.get('run_id')} "
            f"date={date} n={len(tickers)}"
        )
        return job_id

    def complete_job(
        self, job_id: str, n_success: int, elapsed: float,
    ) -> None:
        """标记作业完成，更新成功数和耗时。"""
        with self._conn as conn:
            conn.execute(
                "UPDATE jobs SET n_success=?, elapsed=? WHERE job_id=?",
                (n_success, elapsed, job_id),
            )
            conn.commit()

    def get_job(self, job_id: str) -> Optional[dict]:
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
                "job_id": r[0], "run_id": r[1], "date": r[2],
                "n_tickers": r[3], "n_success": r[4], "elapsed": r[5],
                "data_version": r[6], "strategy_version": r[7],
                "config_hash": r[8],
            }
            for r in rows
        ]

    def get_run_snapshot(self, job_id: str) -> Optional[dict]:
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

    # ── TA Analysis ───────────────────────────────────

    def insert_ta(
        self, job_id: str, result: "StockAnalysisResult",
        version_snapshot: Optional[dict] = None,
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
        run_id = version_snapshot.get("run_id") if version_snapshot else None
        data_version = version_snapshot.get("data_version") if version_snapshot else None
        model_versions = json.dumps(version_snapshot.get("model_versions", {})) if version_snapshot else None
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
    ) -> Optional[dict]:
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
        import time
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

    # ── Kronos Forecast ───────────────────────────────

    def insert_kronos(
        self, job_id: str, result: "KronosForecastResult",
        version_snapshot: Optional[dict] = None,
    ) -> None:
        """写入 Kronos 预测记录。"""
        uncertainty = (
            json.dumps(result.prediction_uncertainty.to_dict())
            if result.prediction_uncertainty else None
        )
        band = json.dumps(result.confidence_band) if result.confidence_band else None
        with self._conn as conn:
            conn.execute(
                "INSERT OR REPLACE INTO kronos_forecast "
                "(job_id, ticker, direction, expected_change, predicted_close, "
                " confidence_band, uncertainty, error, elapsed) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    job_id, result.ticker,
                    result.direction, result.expected_change_pct,
                    result.predicted_close_final,
                    band, uncertainty,
                    result.error, result.elapsed_sec,
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
            {"ticker": r[0], "direction": r[1], "expected_change": r[2],
             "predicted_close": r[3], "error": r[4]}
            for r in rows
        ]

    # ── Signals ───────────────────────────────────────

    def insert_signals(
        self, job_id: str, merged_items: list[dict],
        version_snapshot: Optional[dict] = None,
    ) -> None:
        """写入合并信号记录（含版本信息）。"""
        for item in merged_items:
            pu = item.get("kronos_prediction_uncertainty")
            uncertainty = json.dumps(pu) if pu else None
            ev = item.get("expected_value")
            with self._conn as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO signals "
                    "(job_id, ticker, rank, composite_score, ta_signal, "
                    " ta_confidence, ta_reasoning, kronos_direction, "
                    " kronos_change, uncertainty, ta_error, kronos_error, "
                    " signal_assessment_json, expected_value, conflict) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        job_id, item["ticker"], item.get("rank"),
                        item.get("composite_score"),
                        item.get("ta_signal"), item.get("ta_confidence"),
                        item.get("ta_reasoning", "")[:REASONING_TRUNCATE_LEN],
                        item.get("kronos_direction"),
                        item.get("kronos_change_pct"),
                        uncertainty,
                        item.get("ta_error"), item.get("kronos_error"),
                        json.dumps(item.get("signal_assessment") or {}, ensure_ascii=False),
                        ev,
                        item.get("conflict", ""),
                    ),
                )
                conn.commit()

    def get_signals_by_job(self, job_id: str) -> list[dict]:
        with self._conn as conn:
            rows = conn.execute(
                "SELECT ticker, rank, composite_score, ta_signal, ta_confidence, "
                "       kronos_direction, kronos_change, ta_error, kronos_error, "
                "       expected_value, conflict "
                "FROM signals WHERE job_id=? ORDER BY rank",
                (job_id,),
            ).fetchall()
        return [
            {
                "ticker": r[0], "rank": r[1], "composite_score": r[2],
                "ta_signal": r[3], "ta_confidence": r[4],
                "kronos_direction": r[5], "kronos_change": r[6],
                "ta_error": r[7], "kronos_error": r[8],
                "expected_value": r[9],
                "conflict": r[10],
            }
            for r in rows
        ]

    # ── Decisions ─────────────────────────────────────

    def insert_decision(
        self, job_id: str, ticker: str,
        decision: "InvestmentDecision", thesis: str, risks: list[str],
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

    def get_decision(self, job_id: str, ticker: str) -> Optional[dict]:
        with self._conn as conn:
            row = conn.execute(
                "SELECT decision_json, thesis, risks FROM decisions "
                "WHERE job_id=? AND ticker=?",
                (job_id, ticker),
            ).fetchone()
        if not row:
            return None
        return {
            "decision": json.loads(row[0]),
            "thesis": row[1],
            "risks": json.loads(row[2]) if row[2] else [],
        }

    # ── Raw Reports Index ─────────────────────────────

    def index_raw_report(
        self, job_id: str, ticker: str, file_path: str,
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

    # ── Stats ─────────────────────────────────────────

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
                validated = _validate_table_name(table, _RESEARCH_TABLES)
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

    def get_latest_signal_for_ticker(self, ticker: str) -> Optional[dict]:
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

    # ── Committee Deliberations ─────────────────────────────────────────

    def insert_committee_deliberation(
        self, job_id: str, ticker: str, date: str,
        bull_case: str, bear_case: str,
        recommendation: str, recommendation_confidence: float,
        reasoning: str, agent_consensus: dict,
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
                    job_id, ticker, date,
                    bull_case[:2000], bear_case[:2000],
                    recommendation, recommendation_confidence,
                    reasoning[:2000], consensus_json,
                    time.time(),
                ),
            )
            conn.commit()

    def get_committee_for_ticker(
        self, ticker: str, limit: int = 5,
    ) -> Optional[dict]:
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

    # ── Strategy Run History ────────────────────────────────────────

    def insert_strategy_run(
        self,
        run_at: float,
        strategy: str,
        params: dict,
        tickers: list[str],
        results: list[dict],
        notes: Optional[str] = None,
        config_hash: Optional[str] = None,
    ) -> int:
        """
        记录一次评分策略运行结果到 strategy_runs 表。

        Parameters
        ----------
        run_at      : 运行时间戳（epoch seconds）
        strategy    : 策略名称，如 "linear" / "multiplicative"
        params      : 策略参数 dict（JSON 序列化）
        tickers     : 本次运行涉及的股票代码列表
        results     : 合并结果列表，每项含 ticker + composite_score
        notes       : 备注（可选）
        config_hash : 配置哈希（可选）

        Returns
        -------
        int : 插入的行 ID
        """
        with self._conn as conn:
            cursor = conn.execute(
                "INSERT INTO strategy_runs "
                "(run_at, strategy, params, tickers, results, notes, config_hash) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    run_at,
                    strategy,
                    json.dumps(params, ensure_ascii=False),
                    json.dumps(tickers, ensure_ascii=False),
                    json.dumps(results, ensure_ascii=False, default=str),
                    notes,
                    config_hash,
                ),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def query_strategy_history(
        self,
        strategy: Optional[str] = None,
        limit: int = 20,
    ) -> list[dict]:
        """
        查询评分策略历史运行记录。

        Parameters
        ----------
        strategy : 筛选特定策略（None = 查全部）
        limit    : 最多返回条数

        Returns
        -------
        list[dict] : 按 run_at 降序排列的历史记录
        """
        with self._conn as conn:
            if strategy:
                rows = conn.execute(
                    "SELECT run_at, strategy, params, tickers, results, "
                    "       notes, config_hash "
                    "FROM strategy_runs "
                    "WHERE strategy = ? "
                    "ORDER BY run_at DESC LIMIT ?",
                    (strategy, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT run_at, strategy, params, tickers, results, "
                    "       notes, config_hash "
                    "FROM strategy_runs "
                    "ORDER BY run_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [
            {
                "run_at": r[0],
                "strategy": r[1],
                "params": json.loads(r[2]) if r[2] else {},
                "tickers": json.loads(r[3]) if r[3] else [],
                "n_results": len(json.loads(r[4])) if r[4] else 0,
                "avg_score": self._safe_avg_score(r[4]),
                "notes": r[5],
                "config_hash": r[6],
            }
            for r in rows
        ]

    @staticmethod
    def _safe_avg_score(results_json: Optional[str]) -> Optional[float]:
        """从 JSON 字符串中提取平均 composite_score，非法时返回 None。"""
        if not results_json:
            return None
        try:
            results = json.loads(results_json)
            scores = [
                r.get("composite_score") for r in results
                if isinstance(r, dict) and r.get("composite_score") is not None
            ]
            if not scores:
                return None
            return round(sum(scores) / len(scores), 2)
        except (ValueError, TypeError):
            return None

    # ── Data Snapshots ────────────────────────────────────────────────────

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
                    snapshot_id, cut_date, effective_cut,
                    json.dumps(sources, ensure_ascii=False),
                    description,
                    time.time(),
                ),
            )
            conn.commit()

    def get_data_snapshot(self, snapshot_id: str) -> Optional[dict]:
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

    # ── Walk-Forward Runs ────────────────────────────────────────────────

    def insert_walkforward_run(
        self,
        run_id: str,
        experiment_id: Optional[str],
        ticker: str,
        config: dict,
        total_windows: int,
        valid_windows: int,
        win_rate: float,
        avg_return: float,
        sharpe_annual: float,
        n_records: int,
        elapsed_sec: float,
        snapshot_id: Optional[str] = None,
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
        experiment_id: Optional[str] = None,
        ticker: Optional[str] = None,
        limit: int = 20,
    ) -> list[dict]:
        """查询 walk-forward 运行记录。"""
        conditions = []
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

    # ── Experiments ───────────────────────────────────────────────────────

    def insert_experiment(
        self,
        experiment_id: str,
        full_id: str,
        experiment_type: str,
        hypothesis: dict,
        description: str = "",
        config: Optional[dict] = None,
        data_snapshot_id: Optional[str] = None,
        run_ids: Optional[list[str]] = None,
        result_summary: Optional[dict] = None,
        passed: Optional[bool] = None,
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
                    experiment_id, full_id, experiment_type,
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

    def get_experiment(self, experiment_id: str) -> Optional[dict]:
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
            "experiment_id": row[0], "full_id": row[1],
            "experiment_type": row[2],
            "hypothesis": json.loads(row[3]) if row[3] else {},
            "description": row[4], "config": json.loads(row[5]) if row[5] else {},
            "data_snapshot_id": row[6],
            "run_ids": json.loads(row[7]) if row[7] else [],
            "result_summary": json.loads(row[8]) if row[8] else {},
            "passed": bool(row[9]) if row[9] is not None else None,
            "notes": row[10], "created_at": row[11],
        }

    def list_experiments(
        self,
        experiment_type: Optional[str] = None,
        only_passed: Optional[bool] = None,
        limit: int = 50,
    ) -> list[dict]:
        conditions = []
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
                "experiment_id": r[0], "full_id": r[1],
                "experiment_type": r[2],
                "hypothesis": json.loads(r[3]) if r[3] else {},
                "description": r[4], "data_snapshot_id": r[5],
                "run_ids": json.loads(r[6]) if r[6] else [],
                "result_summary": json.loads(r[7]) if r[7] else {},
                "passed": bool(r[8]) if r[8] is not None else None,
                "created_at": r[9],
            }
            for r in rows
        ]


# ═══════════════════════════════════════════════════════
# 模块级单例
# ═══════════════════════════════════════════════════════

_research: Optional[ResearchDatabase] = None


def get_research() -> ResearchDatabase:
    global _research
    if _research is None:
        _research = ResearchDatabase()
    return _research


def clear_research_singleton() -> None:
    """清除研究数据库单例，使下一次 get_research() 重新初始化。用于测试隔离。"""
    global _research
    _research = None
