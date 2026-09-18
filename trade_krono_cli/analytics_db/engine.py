"""analytics_db.engine — DuckDB 分析引擎。

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

from typing import TYPE_CHECKING, Any

import pandas as pd
from loguru import logger

if TYPE_CHECKING:
    from pathlib import Path

    from trade_krono_cli.config import Settings

from trade_krono_cli.analytics_db._helpers import _duckdb_available, _ensure_duckdb
from trade_krono_cli.analytics_db.paths import ParquetPaths
from trade_krono_cli.analytics_db.writer import ParquetWriter

# Re-export for backward compatibility
__all__ = (
    "ParquetPaths",
    "ParquetWriter",
    "ResearchAnalytics",
    "get_analytics",
    "clear_analytics_singleton",
)


class ResearchAnalytics:
    """DuckDB 分析引擎。

    连接方式：
      - sqlite_scan -> 读取 Research DB 的 jobs / signals / decisions / ta_analysis
      - read_parquet -> 读取 Parquet 文件中的 features / predictions / backtest

    用法示例：
        analytics = ResearchAnalytics(db_path, parquet_paths)
        # 通过注册视图查询 SQLite 研究数据库
        df = analytics.query(
                "SELECT s.ticker, s.rank, s.composite_score, j.date"
                " FROM v_signals s JOIN v_jobs j ON s.job_id = j.job_id"
                " WHERE j.date >= '2026-01-01'"
                " ORDER BY s.rank"
            )
        # 或直接执行 SQL（参数化查询防注入）
        df = analytics.query("SELECT * FROM v_signals WHERE ticker = ?", ("sh.600519",))
    """

    def __init__(
        self,
        db_path: Path,
        parquet_paths: ParquetPaths,
        settings: Settings | None = None,
    ) -> None:
        if not _duckdb_available():
            _ensure_duckdb()
        self._db_path = db_path
        self._paths = parquet_paths
        self._writer = ParquetWriter(parquet_paths)
        self._conn: Any | None = None  # duckdb.DuckDBPyConnection
        self._register_tables()

    def _register_tables(self) -> None:
        """注册 DuckDB 虚拟表：sqlite_scan + Parquet glob。"""
        import duckdb  # type: ignore[import-not-found]

        _ensure_duckdb()
        con = duckdb.connect(database=":memory:", read_only=False)

        # 注册 SQLite 研究数据库的所有表
        for table in (
            "jobs",
            "ta_analysis",
            "kronos_forecast",
            "signals",
            "decisions",
            "raw_reports",
            "backtest_results",
            "strategy_runs",
            "evaluation_results",
        ):
            try:
                con.execute(
                    f"CREATE VIEW IF NOT EXISTS v_{table} AS "
                    f"SELECT * FROM sqlite_scan('{self._db_path}', '{table}')",
                )
            except Exception as e:
                logger.debug(f"⚠️  DuckDB 注册表 {table} 失败: {e}")

        # 注册 Parquet 特征文件
        if self._paths.features_dir.exists():
            try:
                con.execute(
                    f"CREATE VIEW IF NOT EXISTS v_features AS "
                    f"SELECT * FROM read_parquet('{self._paths.features_dir}/**/*.parquet')",
                )
            except Exception as e:
                logger.debug(f"⚠️  DuckDB 注册 features 视图失败: {e}")

        # 注册 Parquet 预测文件
        if self._paths.predictions_dir.exists():
            try:
                con.execute(
                    f"CREATE VIEW IF NOT EXISTS v_predictions AS "
                    f"SELECT * FROM read_parquet('{self._paths.predictions_dir}/**/*.parquet')",
                )
            except Exception as e:
                logger.debug(f"⚠️  DuckDB 注册 predictions 视图失败: {e}")

        # 注册 Parquet 回测文件
        if self._paths.backtest_dir.exists():
            try:
                con.execute(
                    f"CREATE VIEW IF NOT EXISTS v_backtest AS "
                    f"SELECT * FROM read_parquet('{self._paths.backtest_dir}/**/*.parquet')",
                )
            except Exception as e:
                logger.debug(f"⚠️  DuckDB 注册 backtest 视图失败: {e}")

        self._conn = con

    def query(self, sql: str, params: tuple | None = None) -> pd.DataFrame:
        """执行 SQL 查询，返回 Pandas DataFrame。"""
        _ensure_duckdb()
        assert self._conn is not None
        return self._conn.execute(sql, params).fetchdf()

    def query_one(self, sql: str, params: tuple | None = None):
        """执行单值查询。"""
        _ensure_duckdb()
        assert self._conn is not None
        return self._conn.execute(sql, params).fetchone()

    @property
    def conn(self) -> Any | None:  # duckdb.DuckDBPyConnection | None
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    @property
    def writer(self) -> ParquetWriter:
        """返回 Parquet 写入器实例。"""
        return self._writer

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
            "SELECT ticker, decision_json, thesis, risks FROM v_decisions WHERE job_id = ?",
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
        tickers: list[str] | None = None,
        date_gte: str | None = None,
        signal_in: set[str] | None = None,
    ) -> pd.DataFrame:
        """查询 Parquet 特征文件，支持过滤。

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
        job_id: str | None = None,
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
        """计算某作业的横截面 IC（Spearman 秩相关）。

        Parameters
        ----------
        job_id     : 作业 ID
        score_col  : 预测分数列名
        return_col : 实际收益列名

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
            return pd.DataFrame(
                [
                    {
                        "spearman_ic": None,
                        "pearson_ic": None,
                        "n_stocks": 0,
                    },
                ],
            )
        return result

    def factor_rank_regression(
        self,
        job_ids: list[str],
        score_col: str = "composite_score",
        return_col: str = "actual_return_pct",
    ) -> pd.DataFrame:
        """跨多作业进行因子回归分析（IC 时间序列）。

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
        _VALID_VIEWS = frozenset(sources.keys())
        for name, view in sources.items():
            try:
                if view not in _VALID_VIEWS:
                    logger.warning(f"⚠️  未知视图名称跳过: {view}")
                    continue
                row = self.query_one(f"SELECT COUNT(*) FROM {view}")
                stats[name] = row[0] if row else 0
            except Exception as e:
                logger.debug(f"⚠️  统计表查询失败 {name}: {e}")
                stats[name] = 0
        return stats


# ══════════════════════════════════════════════════════════════════════════════
#  模块级工厂函数
# ══════════════════════════════════════════════════════════════════════════════

_analytics: ResearchAnalytics | None = None


def get_analytics(
    db_path: Path | None = None,
    parquet_root: Path | None = None,
    settings: Settings | None = None,
) -> ResearchAnalytics | None:
    """获取全局 Analytics 实例。
    如果 DuckDB 不可用，返回 None（调用方应降级到 SQLite）。
    """
    global _analytics
    if not _duckdb_available():
        return None
    if _analytics is not None:
        return _analytics

    cfg = __import__("trade_krono_cli.config", fromlist=["get_settings"]).get_settings()
    db = db_path or (cfg.cache_dir / "pipeline_cache.db")
    pr = parquet_root or (cfg.cache_dir / "data")
    _analytics = ResearchAnalytics(db, ParquetPaths(pr), settings)
    return _analytics


def clear_analytics_singleton() -> None:
    """清除全局 Analytics 单例（用于测试隔离）。"""
    global _analytics
    if _analytics is not None:
        _analytics.close()
    _analytics = None
