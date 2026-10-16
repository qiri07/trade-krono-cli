"""analytics_db.engine — ResearchAnalytics DuckDB 引擎测试。

DuckDB 为可选依赖，未安装时相关用例自动跳过。
覆盖：
- 初始化 + sqlite_scan 注册（真实 SQLite 研究库）
- query / query_one 参数化查询
- 预置查询：list_jobs / get_signals_by_job / get_decisions_by_job / get_latest_ta
- query_features / query_backtest 过滤组合
- 空结果场景：cross_sectional_ic 空数据返回空壳 DataFrame
- 上下文管理器 / close / writer 属性
- get_analytics 工厂 + clear_analytics_singleton
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd
import pytest
from unittest.mock import patch

from trade_krono_cli.analytics_db._helpers import _HAS_DUCKDB
from trade_krono_cli.analytics_db.engine import (
    clear_analytics_singleton,
    get_analytics,
)
from trade_krono_cli.analytics_db.paths import ParquetPaths

if TYPE_CHECKING:
    from trade_krono_cli.analytics_db.engine import ResearchAnalytics

pytestmark = pytest.mark.skipif(
    not _HAS_DUCKDB,
    reason="DuckDB 未安装（可选依赖），engine 测试跳过",
)


def _make_research_db(tmp_path: Path) -> Path:
    """创建含 jobs/signals/ta_analysis 行的真实研究库（直接 SQL 插入，绕过领域对象）。"""
    import sqlite3
    from uuid import uuid4

    from trade_krono_cli.research_db import ResearchDatabase
    from trade_krono_cli.research_db.schema import CREATE_SCRIPT

    db_path = tmp_path / "research.db"
    db = ResearchDatabase(db_path=db_path, settings=None)
    job_id = uuid4().hex[:12]
    now = 1725000000.0
    conn = db._conn
    conn.execute(
        "INSERT INTO jobs "
        "(job_id, run_id, run_at, date, tickers, n_tickers, n_success, elapsed, "
        " data_version, model_versions, prompt_version, strategy_version, config_hash, "
        " external_repos, notes) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            job_id,
            "run-x",
            now,
            "2026-09-04",
            '["sh.600519"]',
            1,
            1,
            1.5,
            "d1",
            "{}",
            "p1",
            "s1",
            "h1",
            "{}",
            None,
        ),
    )
    conn.execute(
        "INSERT INTO signals "
        "(job_id, ticker, rank, composite_score, ranking_score, ta_signal, "
        " ta_confidence, ta_reasoning, kronos_direction, kronos_change, "
        " uncertainty, ta_error, kronos_error, signal_assessment_json, expected_value, conflict) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            job_id,
            "sh.600519",
            1,
            0.9,
            0.9,
            "BUY",
            0.8,
            "mock",
            "UP",
            2.1,
            None,
            None,
            None,
            "{}",
            0.5,
            "",
        ),
    )
    conn.execute(
        "INSERT INTO ta_analysis "
        "(job_id, ticker, signal, confidence, thesis, risks, error, elapsed) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (job_id, "sh.600519", "BUY", 0.8, "mock", None, None, 1.2),
    )
    conn.execute(
        "INSERT INTO decisions (job_id, ticker, decision_json, thesis, risks) "
        "VALUES (?,?,?,?,?)",
        (job_id, "sh.600519", "{}", "mock", "[]"),
    )
    conn.commit()
    db.close()
    del conn
    return db_path


class TestResearchAnalyticsInit:
    def test_init_registers_tables(self, tmp_path: Path) -> None:
        from trade_krono_cli.analytics_db.engine import ResearchAnalytics

        db_path = _make_research_db(tmp_path)
        analytics = ResearchAnalytics(db_path, ParquetPaths(tmp_path / "data"))
        try:
            assert analytics.conn is not None
            rows = analytics.query("SELECT COUNT(*) AS n FROM v_jobs")
            assert int(rows["n"].iloc[0]) >= 1
        finally:
            analytics.close()

    def test_query_one_returns_single_row(self, tmp_path: Path) -> None:
        from trade_krono_cli.analytics_db.engine import ResearchAnalytics

        db_path = _make_research_db(tmp_path)
        analytics = ResearchAnalytics(db_path, ParquetPaths(tmp_path / "data"))
        try:
            row = analytics.query_one("SELECT COUNT(*) AS n FROM v_signals")
            assert row is not None
            assert row[0] >= 1
        finally:
            analytics.close()

    def test_context_manager_closes_connection(self, tmp_path: Path) -> None:
        from trade_krono_cli.analytics_db.engine import ResearchAnalytics

        db_path = _make_research_db(tmp_path)
        with ResearchAnalytics(db_path, ParquetPaths(tmp_path / "data")) as analytics:
            assert analytics.conn is not None
        assert analytics.conn is None

    def test_close_is_idempotent(self, tmp_path: Path) -> None:
        from trade_krono_cli.analytics_db.engine import ResearchAnalytics

        db_path = _make_research_db(tmp_path)
        analytics = ResearchAnalytics(db_path, ParquetPaths(tmp_path / "data"))
        analytics.close()
        analytics.close()
        assert analytics.conn is None

    def test_writer_property_returns_parquet_writer(self, tmp_path: Path) -> None:
        from trade_krono_cli.analytics_db.engine import ResearchAnalytics
        from trade_krono_cli.analytics_db.writer import ParquetWriter

        db_path = _make_research_db(tmp_path)
        analytics = ResearchAnalytics(db_path, ParquetPaths(tmp_path / "data"))
        try:
            assert isinstance(analytics.writer, ParquetWriter)
        finally:
            analytics.close()


class TestPresetQueries:
    @pytest.fixture()
    def analytics(self, tmp_path: Path) -> ResearchAnalytics:
        from trade_krono_cli.analytics_db.engine import ResearchAnalytics

        db_path = _make_research_db(tmp_path)
        a = ResearchAnalytics(db_path, ParquetPaths(tmp_path / "data"))
        yield a
        a.close()

    def test_list_jobs(self, analytics: ResearchAnalytics) -> None:
        df = analytics.list_jobs(limit=5)
        assert isinstance(df, pd.DataFrame)
        assert "job_id" in df.columns
        assert len(df) >= 1

    def test_get_signals_by_job(self, analytics: ResearchAnalytics) -> None:
        job_row = analytics.list_jobs(limit=1)
        job_id = job_row["job_id"].iloc[0]
        df = analytics.get_signals_by_job(job_id)
        assert len(df) >= 1
        assert df["ticker"].iloc[0] == "sh.600519"

    def test_get_signals_unknown_job(self, analytics: ResearchAnalytics) -> None:
        df = analytics.get_signals_by_job("nonexistent_job")
        assert len(df) == 0

    def test_get_decisions_by_job(self, analytics: ResearchAnalytics) -> None:
        job_row = analytics.list_jobs(limit=1)
        job_id = job_row["job_id"].iloc[0]
        df = analytics.get_decisions_by_job(job_id)
        assert len(df) >= 1

    def test_get_latest_ta(self, analytics: ResearchAnalytics) -> None:
        df = analytics.get_latest_ta("sh.600519")
        assert len(df) >= 1
        assert df["signal"].iloc[0] == "BUY"

    def test_get_latest_ta_unknown_ticker(self, analytics: ResearchAnalytics) -> None:
        df = analytics.get_latest_ta("sh.000000")
        assert len(df) == 0


class TestParquetQueries:
    def test_query_features_with_filters(self, tmp_path: Path) -> None:
        from trade_krono_cli.analytics_db.engine import ResearchAnalytics

        db_path = _make_research_db(tmp_path)
        parquet_root = tmp_path / "data"
        analytics = ResearchAnalytics(db_path, ParquetPaths(parquet_root))
        try:
            writer = analytics.writer
            writer.write_feature(
                "sh.600519",
                "2026-09-04",
                {"ticker": "sh.600519", "eval_date": "2026-09-04", "signal": "BUY"},
            )
            writer.write_feature(
                "sz.000858",
                "2026-09-05",
                {"ticker": "sz.000858", "eval_date": "2026-09-05", "signal": "SELL"},
            )
            # 重新初始化以注册 Parquet 视图
            analytics.close()
            analytics2 = ResearchAnalytics(db_path, ParquetPaths(parquet_root))
            try:
                df = analytics2.query_features(
                    tickers=["sh.600519"],
                    date_gte="2026-09-04",
                    signal_in={"BUY"},
                )
                assert len(df) == 1
                assert df["ticker"].iloc[0] == "sh.600519"
            finally:
                analytics2.close()
        finally:
            analytics.close()

    def test_query_features_no_filters(self, tmp_path: Path) -> None:
        from trade_krono_cli.analytics_db.engine import ResearchAnalytics

        db_path = _make_research_db(tmp_path)
        analytics = ResearchAnalytics(db_path, ParquetPaths(tmp_path / "data"))
        try:
            writer = analytics.writer
            writer.write_feature(
                "sh.600519",
                "2026-09-04",
                {"ticker": "sh.600519", "eval_date": "2026-09-04", "signal": "BUY"},
            )
            analytics.close()
            analytics2 = ResearchAnalytics(db_path, ParquetPaths(tmp_path / "data"))
            try:
                df = analytics2.query_features()
                assert len(df) == 1
            finally:
                analytics2.close()
        finally:
            analytics.close()

    def test_query_backtest_by_job(self, tmp_path: Path) -> None:
        from trade_krono_cli.analytics_db.engine import ResearchAnalytics

        db_path = _make_research_db(tmp_path)
        analytics = ResearchAnalytics(db_path, ParquetPaths(tmp_path / "data"))
        try:
            analytics.writer.write_backtest(
                "job_x",
                [
                    {
                        "job_id": "job_x",
                        "ticker": "sh.600519",
                        "action": "BUY",
                        "entry_price": 100.0,
                        "exit_price": 105.0,
                        "entry_date": "2026-09-04",
                        "exit_date": "2026-09-11",
                        "return_pct": 5.0,
                        "horizon": 5,
                    },
                ],
            )
            analytics.close()
            analytics2 = ResearchAnalytics(db_path, ParquetPaths(tmp_path / "data"))
            try:
                df = analytics2.query_backtest(job_id="job_x")
                assert len(df) == 1
                assert df["job_id"].iloc[0] == "job_x"
                df_all = analytics2.query_backtest()
                assert len(df_all) == 1
            finally:
                analytics2.close()
        finally:
            analytics.close()


class TestCrossSectionalIC:
    def test_ic_empty_returns_placeholder(self, tmp_path: Path) -> None:
        """无匹配数据时返回空壳 DataFrame（n_stocks=0）。"""
        from trade_krono_cli.analytics_db.engine import ResearchAnalytics

        db_path = _make_research_db(tmp_path)
        analytics = ResearchAnalytics(db_path, ParquetPaths(tmp_path / "data"))
        try:
            df = analytics.cross_sectional_ic("nonexistent_job")
            assert isinstance(df, pd.DataFrame)
            assert len(df) == 1
            assert df["n_stocks"].iloc[0] == 0
            assert df["spearman_ic"].iloc[0] is None or pd.isna(df["spearman_ic"].iloc[0])
        finally:
            analytics.close()

    def test_factor_rank_regression_empty(self, tmp_path: Path) -> None:
        from trade_krono_cli.analytics_db.engine import ResearchAnalytics

        db_path = _make_research_db(tmp_path)
        analytics = ResearchAnalytics(db_path, ParquetPaths(tmp_path / "data"))
        try:
            df = analytics.factor_rank_regression(["job_a", "job_b"])
            assert isinstance(df, pd.DataFrame)
            assert len(df) == 0
        finally:
            analytics.close()


class TestDescribe:
    def test_describe_returns_counts(self, tmp_path: Path) -> None:
        from trade_krono_cli.analytics_db.engine import ResearchAnalytics

        db_path = _make_research_db(tmp_path)
        analytics = ResearchAnalytics(db_path, ParquetPaths(tmp_path / "data"))
        try:
            stats = analytics.describe()
            assert isinstance(stats, dict)
            assert stats["jobs"] >= 1
            assert stats["signals"] >= 1
        finally:
            analytics.close()


class TestAnalyticsFactory:
    def test_get_analytics_returns_instance(self, tmp_path: Path) -> None:
        clear_analytics_singleton()
        try:
            result = get_analytics(db_path=tmp_path / "r.db", parquet_root=tmp_path / "d")
            assert result is not None
        finally:
            clear_analytics_singleton()

    def test_get_analytics_returns_singleton(self, tmp_path: Path) -> None:
        clear_analytics_singleton()
        try:
            a1 = get_analytics(db_path=tmp_path / "r.db", parquet_root=tmp_path / "d")
            a2 = get_analytics(db_path=tmp_path / "other.db", parquet_root=tmp_path / "d2")
            assert a1 is a2
        finally:
            clear_analytics_singleton()

    def test_get_analytics_returns_none_when_duckdb_missing(self, tmp_path: Path) -> None:
        clear_analytics_singleton()
        with patch(
            "trade_krono_cli.analytics_db.engine._duckdb_available",
            return_value=False,
        ):
            assert get_analytics() is None

    def test_clear_analytics_singleton_closes(self, tmp_path: Path) -> None:
        clear_analytics_singleton()
        a = get_analytics(db_path=tmp_path / "r.db", parquet_root=tmp_path / "d")
        assert a is not None
        clear_analytics_singleton()
        # 单例已清空
        import trade_krono_cli.analytics_db.engine as engine_mod

        assert engine_mod._analytics is None
