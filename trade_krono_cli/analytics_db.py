"""
Analytics 引擎 — DuckDB + Parquet 分析层。

职责：
  · 对 Parquet 文件执行大规模分析查询（横截面 IC、回测聚合、因子分析）
  · 通过 sqlite_scan 访问 Research DB 元数据（jobs / signals / decisions）
  · 写入 Parquet 文件（features / predictions / backtest）

架构：
  SQLite  ←─ 事务写入（jobs, signals, decisions, raw_reports）
                │
   sqlite_scan() ─┤
                │
  DuckDB  ──→  分析查询（IC、回测聚合、横截面分析）
                │
  Parquet ←──  文件存储（features, predictions, backtest results）

DuckDB 未安装时自动降级为 SQLite 直接查询（向后兼容）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import pandas as pd
from loguru import logger

if TYPE_CHECKING:
    from trade_krono_cli.config import Settings
    from trade_krono_cli.research_db import ResearchDatabase

# ── 可选 DuckDB 导入 ───────────────────────────────────────────────────────────
try:
    import duckdb
    _HAS_DUCKDB = True
except ImportError:
    _HAS_DUCKDB = False
    duckdb = None  # type: ignore


def _duckdb_available() -> bool:
    return _HAS_DUCKDB


def _ensure_duckdb() -> None:
    if not _HAS_DUCKDB:
        raise RuntimeError(
            "DuckDB 未安装，无法使用 Analytics 引擎。\n"
            "请运行: pip install duckdb 或 uv add duckdb"
        )


# ══════════════════════════════════════════════════════════════════════════════
#  Parquet 文件路径构造
# ══════════════════════════════════════════════════════════════════════════════

class ParquetPaths:
    """管理 Parquet 数据文件的目录结构。"""

    def __init__(self, data_root: Path):
        self.data_root = data_root
        self.features_dir = data_root / "features"
        self.predictions_dir = data_root / "predictions"
        self.backtest_dir = data_root / "backtest"
        for d in (self.features_dir, self.predictions_dir, self.backtest_dir):
            d.mkdir(parents=True, exist_ok=True)

    def feature_path(self, ticker: str, date: str) -> Path:
        """data/features/{year}/{month}/{ticker}_{date}.parquet"""
        safe = ticker.replace(".", "_")
        from datetime import datetime
        dt = datetime.strptime(date, "%Y-%m-%d")
        p = self.features_dir / str(dt.year) / f"{dt.month:02d}"
        p.mkdir(parents=True, exist_ok=True)
        return p / f"{safe}_{date}.parquet"

    def prediction_path(self, ticker: str, date: str, pred_len: int) -> Path:
        """data/predictions/{year}/{month}/{ticker}_{date}_{predlen}.parquet"""
        safe = ticker.replace(".", "_")
        from datetime import datetime
        dt = datetime.strptime(date, "%Y-%m-%d")
        p = self.predictions_dir / str(dt.year) / f"{dt.month:02d}"
        p.mkdir(parents=True, exist_ok=True)
        return p / f"{safe}_{date}_{pred_len}.parquet"

    def backtest_path(self, job_id: str) -> Path:
        """data/backtest/{job_id}.parquet"""
        p = self.backtest_dir
        p.mkdir(parents=True, exist_ok=True)
        return p / f"{job_id}.parquet"


# ══════════════════════════════════════════════════════════════════════════════
#  Parquet 写入器
# ══════════════════════════════════════════════════════════════════════════════

class ParquetWriter:
    """将分析结果写入 Parquet 文件。"""

    def __init__(self, paths: ParquetPaths):
        self.paths = paths

    def write_feature(
        self, ticker: str, date: str,
        data: dict,
    ) -> Path:
        """写入单只股票的 TA 分析特征到 Parquet。"""
        path = self.paths.feature_path(ticker, date)
        df = pd.DataFrame([data])
        df.to_parquet(path, engine="pyarrow", index=False)
        logger.debug(f"📦 特征 Parquet 已写入: {path}")
        return path

    def write_prediction(
        self, ticker: str, date: str, pred_len: int,
        data: dict,
    ) -> Path:
        """写入单只股票的 Kronos 预测到 Parquet。"""
        path = self.paths.prediction_path(ticker, date, pred_len)
        df = pd.DataFrame([data])
        df.to_parquet(path, engine="pyarrow", index=False)
        logger.debug(f"📦 预测 Parquet 已写入: {path}")
        return path

    def write_backtest(
        self, job_id: str,
        records: list[dict],
    ) -> Path:
        """写入回测结果到 Parquet（支持多记录）。"""
        path = self.paths.backtest_path(job_id)
        if records:
            df = pd.DataFrame(records)
        else:
            df = pd.DataFrame(columns=["ticker", "action", "entry_price", "exit_price",
                                       "entry_date", "exit_date", "return_pct", "horizon"])
        df.to_parquet(path, engine="pyarrow", index=False)
        logger.debug(f"📦 回测 Parquet 已写入: {path} ({len(records)} 条)")
        return path


# ══════════════════════════════════════════════════════════════════════════════
#  DuckDB Analytics Engine
# ══════════════════════════════════════════════════════════════════════════════

class ResearchAnalytics:
    """
    DuckDB 分析引擎。

    连接方式：
      - sqlite_scan -> 读取 Research DB 的 jobs / signals / decisions / ta_analysis
      - read_parquet -> 读取 Parquet 文件中的 features / predictions / backtest

    用法示例：
        analytics = ResearchAnalytics(db_path, parquet_paths)
        df = analytics.query(
            "SELECT s.ticker, s.rank, s.composite_score, j.date"
            " FROM sqlite_scan('pipeline_cache.db', 'signals') s"
            " JOIN sqlite_scan('pipeline_cache.db', 'jobs') j"
            "   ON s.job_id = j.job_id"
            " WHERE j.date >= '2026-01-01'"
            " ORDER BY s.rank"
        )
    """

    def __init__(
        self,
        db_path: Path,
        parquet_paths: ParquetPaths,
        settings: Optional["Settings"] = None,
    ):
        if not _duckdb_available():
            _ensure_duckdb()
        self._db_path = db_path
        self._paths = parquet_paths
        self._conn: Optional[duckdb.DuckDBPyConnection] = None
        self._register_tables()

    def _register_tables(self) -> None:
        """注册 DuckDB 虚拟表：sqlite_scan + Parquet glob。"""
        _ensure_duckdb()
        con = duckdb.connect(database=":memory:", read_only=False)

        # 注册 SQLite 研究数据库的所有表
        for table in ("jobs", "ta_analysis", "kronos_forecast",
                      "signals", "decisions", "raw_reports",
                      "backtest_results", "strategy_runs",
                      "evaluation_results"):
            try:
                con.execute(
                    f"CREATE VIEW IF NOT EXISTS v_{table} AS "
                    f"SELECT * FROM sqlite_scan('{self._db_path}', '{table}')"
                )
            except Exception as e:
                logger.debug(f"⚠️  DuckDB 注册表 {table} 失败: {e}")

        # 注册 Parquet 特征文件
        if self._paths.features_dir.exists():
            try:
                con.execute(
                    f"CREATE VIEW IF NOT EXISTS v_features AS "
                    f"SELECT * FROM read_parquet('{self._paths.features_dir}/**/*.parquet')"
                )
            except Exception as e:
                logger.debug(f"⚠️  DuckDB 注册 features 视图失败: {e}")

        # 注册 Parquet 预测文件
        if self._paths.predictions_dir.exists():
            try:
                con.execute(
                    f"CREATE VIEW IF NOT EXISTS v_predictions AS "
                    f"SELECT * FROM read_parquet('{self._paths.predictions_dir}/**/*.parquet')"
                )
            except Exception as e:
                logger.debug(f"⚠️  DuckDB 注册 predictions 视图失败: {e}")

        # 注册 Parquet 回测文件
        if self._paths.backtest_dir.exists():
            try:
                con.execute(
                    f"CREATE VIEW IF NOT EXISTS v_backtest AS "
                    f"SELECT * FROM read_parquet('{self._paths.backtest_dir}/**/*.parquet')"
                )
            except Exception as e:
                logger.debug(f"⚠️  DuckDB 注册 backtest 视图失败: {e}")

        self._conn = con

    def query(self, sql: str, params: Optional[tuple] = None) -> pd.DataFrame:
        """执行 SQL 查询，返回 Pandas DataFrame。"""
        _ensure_duckdb()
        assert self._conn is not None
        return self._conn.execute(sql, params).fetchdf()

    def query_one(self, sql: str, params: Optional[tuple] = None):
        """执行单值查询。"""
        _ensure_duckdb()
        assert self._conn is not None
        return self._conn.execute(sql, params).fetchone()

    @property
    def conn(self) -> Optional[duckdb.DuckDBPyConnection]:
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    # ── 预置查询（常用分析场景）────────────────────────────────────────────────

    def list_jobs(self, limit: int = 20) -> pd.DataFrame:
        """列出最近分析作业。"""
        return self.query(
            "SELECT job_id, date, n_tickers, n_success, elapsed, "
            "       data_version, strategy_version, config_hash "
            "FROM v_jobs ORDER BY run_at DESC LIMIT ?",
            (limit,),
        )

    def get_signals_by_job(self, job_id: str) -> pd.DataFrame:
        """获取某作业的合并信号。"""
        return self.query(
            "SELECT ticker, rank, composite_score, ta_signal, ta_confidence, "
            "       kronos_direction, kronos_change "
            "FROM v_signals WHERE job_id = ? ORDER BY rank",
            (job_id,),
        )

    def get_decisions_by_job(self, job_id: str) -> pd.DataFrame:
        """获取某作业的投资决策。"""
        return self.query(
            "SELECT ticker, decision_json, thesis, risks "
            "FROM v_decisions WHERE job_id = ?",
            (job_id,),
        )

    def get_latest_ta(self, ticker: str) -> pd.DataFrame:
        """获取某股票的最新 TA 分析。"""
        return self.query(
            """
            SELECT ta.ticker, ta.signal, ta.confidence, ta.thesis, ta.llm_request,
                   j.date, j.job_id
            FROM v_ta_analysis ta
            JOIN v_jobs j ON ta.job_id = j.job_id
            WHERE ta.ticker = ? AND ta.error IS NULL
            ORDER BY j.run_at DESC
            LIMIT 1
            """,
            (ticker,),
        )

    def query_features(
        self,
        tickers: Optional[list[str]] = None,
        date_gte: Optional[str] = None,
        signal_in: Optional[set[str]] = None,
    ) -> pd.DataFrame:
        """
        查询 Parquet 特征文件，支持过滤。

        Parameters
        ----------
        tickers    : 股票代码列表（可选）
        date_gte   : 起始日期（可选）
        signal_in  : 信号值集合（可选，如 {"BUY", "HOLD"}）
        """
        conditions = []
        params = []
        if tickers:
            placeholders = ",".join("?" * len(tickers))
            conditions.append(f"ticker IN ({placeholders})")
            params.extend(tickers)
        if date_gte:
            conditions.append("eval_date >= ?")
            params.append(date_gte)
        if signal_in:
            placeholders = ",".join("?" * len(signal_in))
            conditions.append(f"signal IN ({placeholders})")
            params.extend(signal_in)

        sql = "SELECT * FROM v_features"
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY eval_date DESC"
        return self.query(sql, tuple(params) if params else None)

    def query_backtest(
        self,
        job_id: Optional[str] = None,
    ) -> pd.DataFrame:
        """查询 Parquet 回测结果文件。"""
        sql = "SELECT * FROM v_backtest"
        if job_id:
            sql += " WHERE job_id = ?"
            return self.query(sql, (job_id,))
        return self.query(sql)

    def cross_sectional_ic(
        self,
        job_id: str,
        score_col: str = "composite_score",
        return_col: str = "actual_return_pct",
    ) -> pd.DataFrame:
        """
        计算某作业的横截面 IC（Spearman 秩相关）。

        Parameters
        ----------
        job_id     : 作业 ID
        score_col  : 预测分数列名
        return_col : 实际收益列名
        """
        sql = f"""
            SELECT CORR(
                "{score_col}",
                "{return_col}"
            ) AS pearson_ic,
            COUNT(*) AS n_stocks
            FROM v_signals s
            JOIN v_ta_analysis t ON s.job_id = t.job_id AND s.ticker = t.ticker
            WHERE s.job_id = ?
              AND s.{score_col} IS NOT NULL
              AND t.{return_col} IS NOT NULL
        """
        # 注意：DuckDB 的 CORR 是 Pearson，Spearman 需用 rank 包装
        sql_spearman = f"""
            SELECT CORR(
                RANK() OVER (ORDER BY "{score_col}")::DOUBLE,
                RANK() OVER (ORDER BY "{return_col}")::DOUBLE
            ) AS spearman_ic,
            COUNT(*) AS n_stocks
            FROM v_signals s
            JOIN v_ta_analysis t ON s.job_id = t.job_id AND s.ticker = t.ticker
            WHERE s.job_id = ?
              AND s.{score_col} IS NOT NULL
              AND t.{return_col} IS NOT NULL
        """
        result = self.query(sql_spearman, (job_id,))
        if result.empty:
            return pd.DataFrame([{
                "spearman_ic": None,
                "pearson_ic": None,
                "n_stocks": 0,
            }])
        return result

    def factor_rank_regression(
        self,
        job_ids: list[str],
        score_col: str = "composite_score",
        return_col: str = "actual_return_pct",
    ) -> pd.DataFrame:
        """
        跨多作业进行因子回归分析（IC 时间序列）。

        返回每个 job_id 的 IC 和 ICIR。
        """
        placeholders = ",".join("?" * len(job_ids))
        sql = f"""
            SELECT j.date,
                   CORR(s.{score_col}, t.{return_col}) AS pearson_ic,
                   COUNT(*) AS n_stocks
            FROM v_jobs j
            JOIN v_signals s ON s.job_id = j.job_id
            JOIN v_ta_analysis t ON t.job_id = s.job_id AND t.ticker = s.ticker
            WHERE j.job_id IN ({placeholders})
              AND s.{score_col} IS NOT NULL
              AND t.{return_col} IS NOT NULL
            GROUP BY j.date
            ORDER BY j.date
        """
        return self.query(sql, tuple(job_ids))

    def describe(self) -> dict:
        """返回各数据源的统计摘要。"""
        stats: dict[str, int] = {}
        if not _duckdb_available():
            return stats

        sources = {
            "jobs": "v_jobs",
            "ta_analysis": "v_ta_analysis",
            "kronos_forecast": "v_kronos_forecast",
            "signals": "v_signals",
            "decisions": "v_decisions",
            "features_parquet": "v_features",
            "predictions_parquet": "v_predictions",
            "backtest_parquet": "v_backtest",
        }
        for name, view in sources.items():
            try:
                row = self.query_one(f"SELECT COUNT(*) FROM {view}")
                stats[name] = row[0] if row else 0
            except Exception:
                stats[name] = 0
        return stats


# ══════════════════════════════════════════════════════════════════════════════
#  模块级工厂函数
# ══════════════════════════════════════════════════════════════════════════════

_analytics: Optional[ResearchAnalytics] = None


def get_analytics(
    db_path: Optional[Path] = None,
    parquet_root: Optional[Path] = None,
    settings: Optional["Settings"] = None,
) -> Optional[ResearchAnalytics]:
    """
    获取全局 Analytics 实例。
    如果 DuckDB 不可用，返回 None（调用方应降级到 SQLite）。
    """
    global _analytics
    if not _duckdb_available():
        return None
    if _analytics is not None:
        return _analytics

    cfg = settings or __import__("trade_krono_cli.config", fromlist=["get_settings"]).get_settings()
    db = db_path or (cfg.cache_dir / "pipeline_cache.db")
    pr = parquet_root or (cfg.cache_dir / "data")
    _analytics = ResearchAnalytics(db, ParquetPaths(pr), settings)
    return _analytics


def clear_analytics_singleton() -> None:
    global _analytics
    if _analytics is not None:
        _analytics.close()
    _analytics = None
