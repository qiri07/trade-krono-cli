"""
缓存层 + 研究数据库 — 概念分离，同一 SQLite 文件。

缓存（Cache）: TTL 驱动，性能优化
  └─ kline_cache | ta_cache | kronos_cache

研究数据库（ResearchDatabase）: 永久存储，可查询分析
  └─ jobs | ta_analysis | kronos_forecast
     │ signals | decisions | raw_reports
     └─ backtest_results | strategy_runs（预留）
"""
from __future__ import annotations

import json
import sqlite3
import time
from io import BytesIO
from pathlib import Path
from typing import Optional
from uuid import uuid4

import pandas as pd

from loguru import logger
from trade_krono_cli.config import get_settings


# ═══════════════════════════════════════════════════════
# Cache — TTL 驱动，性能优化
# ═══════════════════════════════════════════════════════

class Cache:
    """SQLite 缓存，支持 K 线、TA 结果、Kronos 预测三种类型。TTL 过期后自动失效。"""

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or (
            get_settings().cache_dir / "pipeline_cache.db"
        )
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS kline_cache (
                    ticker    TEXT NOT NULL,
                    start     TEXT NOT NULL,
                    end       TEXT NOT NULL,
                    freq      TEXT NOT NULL,
                    ttl       REAL NOT NULL,
                    data      BLOB NOT NULL,
                    created   REAL NOT NULL,
                    PRIMARY KEY (ticker, start, end, freq)
                );

                CREATE TABLE IF NOT EXISTS ta_cache (
                    ticker   TEXT NOT NULL,
                    date     TEXT NOT NULL,
                    ttl      REAL NOT NULL,
                    data     BLOB NOT NULL,
                    created  REAL NOT NULL,
                    PRIMARY KEY (ticker, date)
                );

                CREATE TABLE IF NOT EXISTS kronos_cache (
                    ticker    TEXT NOT NULL,
                    date      TEXT NOT NULL,
                    pred_len  INTEGER NOT NULL,
                    ttl       REAL NOT NULL,
                    data      BLOB NOT NULL,
                    created   REAL NOT NULL,
                    PRIMARY KEY (ticker, date, pred_len)
                );
            """)

    # ── K 线缓存 ──────────────────────────────────────

    def get_kline(
        self, ticker: str, start: str, end: str, freq: str
    ) -> Optional[pd.DataFrame]:
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT data, created, ttl FROM kline_cache "
                "WHERE ticker=? AND start=? AND end=? AND freq=?",
                (ticker, start, end, freq),
            ).fetchone()
        if row is None:
            return None
        data, created, ttl = row
        if time.time() - created > ttl:
            return None
        return pd.read_pickle(BytesIO(data))

    def set_kline(
        self,
        ticker: str,
        start: str,
        end: str,
        freq: str,
        df: pd.DataFrame,
        ttl: float = 86400,
    ) -> None:
        buf = BytesIO()
        df.to_pickle(buf)
        buf.seek(0)
        created = time.time()
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO kline_cache "
                "(ticker, start, end, freq, ttl, data, created) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ticker, start, end, freq, ttl, buf.read(), created),
            )
            conn.commit()
        logger.debug(f"📦 K线缓存写入: {ticker} {start}~{end}")

    # ── TA 缓存 ───────────────────────────────────────

    def get_ta(self, ticker: str, date: str) -> Optional[dict]:
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT data, created, ttl FROM ta_cache "
                "WHERE ticker=? AND date=?",
                (ticker, date),
            ).fetchone()
        if row is None:
            return None
        data, created, ttl = row
        if time.time() - created > ttl:
            return None
        return json.loads(data)

    def set_ta(self, ticker: str, date: str, result: dict, ttl: float = 86400) -> None:
        created = time.time()
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO ta_cache "
                "(ticker, date, ttl, data, created) "
                "VALUES (?, ?, ?, ?, ?)",
                (ticker, date, ttl,
                 json.dumps(result, ensure_ascii=False).encode(), created),
            )
            conn.commit()

    # ── Kronos 缓存 ───────────────────────────────────

    def get_kronos(
        self, ticker: str, date: str, pred_len: int
    ) -> Optional[dict]:
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT data, created, ttl FROM kronos_cache "
                "WHERE ticker=? AND date=? AND pred_len=?",
                (ticker, date, pred_len),
            ).fetchone()
        if row is None:
            return None
        data, created, ttl = row
        if time.time() - created > ttl:
            return None
        return json.loads(data)

    def set_kronos(
        self, ticker: str, date: str, pred_len: int,
        result: dict, ttl: float = 86400,
    ) -> None:
        created = time.time()
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO kronos_cache "
                "(ticker, date, pred_len, ttl, data, created) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (ticker, date, pred_len, ttl,
                 json.dumps(result, ensure_ascii=False).encode(), created),
            )
            conn.commit()

    # ── 工具方法 ──────────────────────────────────────

    def clear_all(self) -> int:
        """清除所有缓存表（不影响 research 表）。"""
        with sqlite3.connect(self._db_path) as conn:
            count = 0
            for table in ("kline_cache", "ta_cache", "kronos_cache"):
                r = conn.execute(f"DELETE FROM {table}").rowcount
                count += r
            conn.commit()
        logger.info(f"🧹 清除缓存 {count} 条（research 数据不受影响）")
        return count

    def stats(self) -> dict:
        """返回各缓存表统计。"""
        with sqlite3.connect(self._db_path) as conn:
            return {
                f"cache_{t}": conn.execute(
                    f"SELECT COUNT(*) FROM {t}"
                ).fetchone()[0]
                for t in ("kline_cache", "ta_cache", "kronos_cache")
            }


# ═══════════════════════════════════════════════════════
# ResearchDatabase — 永久研究记录，无 TTL
# ═══════════════════════════════════════════════════════

class ResearchDatabase:
    """
    投研数据持久化层。

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
    """

    _RESEARCH_TABLES = (
        "jobs", "ta_analysis", "kronos_forecast",
        "signals", "decisions", "raw_reports",
        "backtest_results", "strategy_runs",
    )

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or (
            get_settings().cache_dir / "pipeline_cache.db"
        )
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.executescript("""
                -- 每次运行作业
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id    TEXT PRIMARY KEY,
                    run_at    REAL NOT NULL,
                    date      TEXT NOT NULL,
                    tickers   TEXT NOT NULL,    -- JSON 列表
                    n_tickers INTEGER NOT NULL,
                    n_success INTEGER NOT NULL,
                    elapsed   REAL NOT NULL,
                    notes     TEXT
                );

                -- TA 分析结构化摘要
                CREATE TABLE IF NOT EXISTS ta_analysis (
                    job_id    TEXT NOT NULL,
                    ticker    TEXT NOT NULL,
                    signal    TEXT,
                    confidence REAL,
                    thesis    TEXT,
                    risks     TEXT,            -- JSON 列表
                    error     TEXT,
                    elapsed   REAL,
                    PRIMARY KEY (job_id, ticker),
                    FOREIGN KEY (job_id) REFERENCES jobs(job_id)
                );

                -- Kronos 预测结构化摘要
                CREATE TABLE IF NOT EXISTS kronos_forecast (
                    job_id            TEXT NOT NULL,
                    ticker            TEXT NOT NULL,
                    direction         TEXT,
                    expected_change   REAL,
                    predicted_close   REAL,
                    confidence_band   TEXT,    -- JSON {"low": x, "high": y}
                    uncertainty       TEXT,    -- JSON prediction_uncertainty
                    error             TEXT,
                    elapsed           REAL,
                    PRIMARY KEY (job_id, ticker),
                    FOREIGN KEY (job_id) REFERENCES jobs(job_id)
                );

                -- 合并后的综合信号（最终排名结果）
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
                    uncertainty       TEXT,    -- JSON
                    ta_error          TEXT,
                    kronos_error      TEXT,
                    PRIMARY KEY (job_id, ticker),
                    FOREIGN KEY (job_id) REFERENCES jobs(job_id)
                );

                -- 结构化 InvestmentDecision 完整记录
                CREATE TABLE IF NOT EXISTS decisions (
                    job_id        TEXT NOT NULL,
                    ticker        TEXT NOT NULL,
                    decision_json TEXT NOT NULL,  -- JSON: InvestmentDecision.to_dict()
                    thesis        TEXT,
                    risks         TEXT,           -- JSON 列表
                    PRIMARY KEY (job_id, ticker),
                    FOREIGN KEY (job_id) REFERENCES jobs(job_id)
                );

                -- 原始报告文件索引
                CREATE TABLE IF NOT EXISTS raw_reports (
                    job_id   TEXT NOT NULL,
                    ticker   TEXT NOT NULL,
                    path     TEXT NOT NULL,       -- 磁盘路径
                    reports  TEXT,                -- JSON: {alias: length}
                    PRIMARY KEY (job_id, ticker),
                    FOREIGN KEY (job_id) REFERENCES jobs(job_id)
                );

                -- 预留表
                CREATE TABLE IF NOT EXISTS backtest_results (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id      TEXT NOT NULL,
                    strategy    TEXT NOT NULL,
                    symbols     TEXT,              -- JSON 列表
                    start_date  TEXT,
                    end_date    TEXT,
                    results     TEXT,              -- JSON
                    created_at  REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS strategy_runs (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_at      REAL NOT NULL,
                    strategy    TEXT NOT NULL,
                    params      TEXT,              -- JSON
                    tickers     TEXT,              -- JSON 列表
                    results     TEXT,              -- JSON
                    notes       TEXT
                );
            """)

    # ── Jobs ──────────────────────────────────────────

    def create_job(
        self, date: str, tickers: list[str],
        notes: Optional[str] = None,
    ) -> str:
        """创建新分析作业，返回 job_id。"""
        job_id = str(uuid4())[:12]
        run_at = time.time()
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "INSERT INTO jobs (job_id, run_at, date, tickers, n_tickers, "
                "n_success, elapsed, notes) VALUES (?,?,?,?,?,?,?,?)",
                (job_id, run_at, date,
                 json.dumps(tickers, ensure_ascii=False),
                 len(tickers), 0, 0.0, notes),
            )
            conn.commit()
        logger.info(f"📋 研究作业创建: job={job_id} date={date} n={len(tickers)}")
        return job_id

    def complete_job(
        self, job_id: str, n_success: int, elapsed: float,
    ) -> None:
        """标记作业完成，更新成功数和耗时。"""
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "UPDATE jobs SET n_success=?, elapsed=? WHERE job_id=?",
                (n_success, elapsed, job_id),
            )
            conn.commit()

    def get_job(self, job_id: str) -> Optional[dict]:
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT job_id, run_at, date, tickers, n_tickers, "
                "n_success, elapsed, notes FROM jobs WHERE job_id=?",
                (job_id,),
            ).fetchone()
        if not row:
            return None
        return {
            "job_id": row[0],
            "run_at": row[1],
            "date": row[2],
            "tickers": json.loads(row[3]),
            "n_tickers": row[4],
            "n_success": row[5],
            "elapsed": row[6],
            "notes": row[7],
        }

    def list_jobs(self, limit: int = 20) -> list[dict]:
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT job_id, run_at, date, n_tickers, n_success, elapsed "
                "FROM jobs ORDER BY run_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {
                "job_id": r[0], "run_at": r[1], "date": r[2],
                "n_tickers": r[3], "n_success": r[4], "elapsed": r[5],
            }
            for r in rows
        ]

    # ── TA Analysis ───────────────────────────────────

    def insert_ta(self, job_id: str, result: "StockAnalysisResult") -> None:
        from trade_krono_cli.ta_runner import StockAnalysisResult  # 延迟避免循环
        risks = (
            json.dumps(result.investment_decision.risks, ensure_ascii=False)
            if result.investment_decision else None
        )
        thesis = (
            result.investment_decision.thesis
            if result.investment_decision else (result.reasoning or "")[:500]
        )
        with sqlite3.connect(self._db_path) as conn:
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
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT ticker, signal, confidence, error, elapsed "
                "FROM ta_analysis WHERE job_id=? ORDER BY ticker",
                (job_id,),
            ).fetchall()
        return [
            {"ticker": r[0], "signal": r[1], "confidence": r[2],
             "error": r[3], "elapsed": r[4]}
            for r in rows
        ]

    # ── Kronos Forecast ───────────────────────────────

    def insert_kronos(self, job_id: str, result) -> None:
        uncertainty = (
            json.dumps(result.prediction_uncertainty.to_dict())
            if result.prediction_uncertainty else None
        )
        band = json.dumps(result.confidence_band) if result.confidence_band else None
        with sqlite3.connect(self._db_path) as conn:
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
        with sqlite3.connect(self._db_path) as conn:
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

    # ── Signals（合并后的综合信号）─────────────────────

    def insert_signals(self, job_id: str, merged_items: list[dict]) -> None:
        for item in merged_items:
            pu = item.get("kronos_prediction_uncertainty")
            uncertainty = (
                json.dumps(pu) if pu else None
            )
            with sqlite3.connect(self._db_path) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO signals "
                    "(job_id, ticker, rank, composite_score, ta_signal, "
                    " ta_confidence, ta_reasoning, kronos_direction, "
                    " kronos_change, uncertainty, ta_error, kronos_error) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        job_id, item["ticker"], item.get("rank"),
                        item.get("composite_score"),
                        item.get("ta_signal"), item.get("ta_confidence"),
                        item.get("ta_reasoning", "")[:500],
                        item.get("kronos_direction"),
                        item.get("kronos_change_pct"),
                        uncertainty,
                        item.get("ta_error"), item.get("kronos_error"),
                    ),
                )
                conn.commit()

    def get_signals_by_job(self, job_id: str) -> list[dict]:
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT ticker, rank, composite_score, ta_signal, ta_confidence, "
                "       kronos_direction, kronos_change, ta_error, kronos_error "
                "FROM signals WHERE job_id=? ORDER BY rank",
                (job_id,),
            ).fetchall()
        return [
            {
                "ticker": r[0], "rank": r[1], "composite_score": r[2],
                "ta_signal": r[3], "ta_confidence": r[4],
                "kronos_direction": r[5], "kronos_change": r[6],
                "ta_error": r[7], "kronos_error": r[8],
            }
            for r in rows
        ]

    # ── Decisions ─────────────────────────────────────

    def insert_decision(
        self, job_id: str, ticker: str,
        decision, thesis: str, risks: list[str],
    ) -> None:
        decision_json = json.dumps(decision.to_dict(), ensure_ascii=False)
        risks_json = json.dumps(risks, ensure_ascii=False)
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO decisions "
                "(job_id, ticker, decision_json, thesis, risks) "
                "VALUES (?,?,?,?,?)",
                (job_id, ticker, decision_json, thesis, risks_json),
            )
            conn.commit()

    def get_decision(self, job_id: str, ticker: str) -> Optional[dict]:
        with sqlite3.connect(self._db_path) as conn:
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
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO raw_reports "
                "(job_id, ticker, path, reports) VALUES (?,?,?,?)",
                (job_id, ticker, file_path, reports_json),
            )
            conn.commit()

    # ── Stats ─────────────────────────────────────────

    def stats(self) -> dict:
        """返回各 research 表统计。"""
        with sqlite3.connect(self._db_path) as conn:
            result = {}
            for table in self._RESEARCH_TABLES:
                try:
                    count = conn.execute(
                        f"SELECT COUNT(*) FROM {table}"
                    ).fetchone()[0]
                    result[f"research_{table}"] = count
                except sqlite3.OperationalError:
                    result[f"research_{table}"] = 0
            return result

    def query_history(
        self, ticker: str, limit: int = 20,
    ) -> list[dict]:
        """查询某只股票的历史分析记录（合并 signals + decisions）。"""
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT j.date, s.rank, s.composite_score,
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
                "date": r[0], "rank": r[1],
                "composite_score": r[2],
                "ta_signal": r[3], "ta_confidence": r[4],
                "kronos_direction": r[5], "kronos_change": r[6],
                "decision": json.loads(r[7]) if r[7] else None,
            }
            for r in rows
        ]


# ═══════════════════════════════════════════════════════
# 模块级单例
# ═══════════════════════════════════════════════════════

_cache: Optional[Cache] = None
_research: Optional[ResearchDatabase] = None


def get_cache() -> Cache:
    global _cache
    if _cache is None:
        _cache = Cache()
    return _cache


def get_research() -> ResearchDatabase:
    global _research
    if _research is None:
        _research = ResearchDatabase()
    return _research
