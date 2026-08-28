"""测试 ResearchAnalytics — DuckDB 分析引擎。"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from trade_krono_cli.analytics_db import (
    ParquetPaths,
    ParquetWriter,
    ResearchAnalytics,
    _duckdb_available,
    clear_analytics_singleton,
    get_analytics,
)
from trade_krono_cli.research_db import ResearchDatabase, clear_research_singleton

# ══════════════════════════════════════════════════════════════════════════════
#  跳过测试：DuckDB 未安装
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.skipif(not _duckdb_available(), reason="DuckDB not installed")
class TestResearchAnalytics:
    """DuckDB 可用时的完整测试。"""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.tmp = tmp_path
        self.db_path = tmp_path / "test_research.db"
        self.parquet_root = tmp_path / "data"
        clear_research_singleton()
        clear_analytics_singleton()

    def test_create_analytics(self):
        """创建 Analytics 实例应成功。"""
        _research = ResearchDatabase(db_path=self.db_path)
        analytics = ResearchAnalytics(self.db_path, ParquetPaths(self.parquet_root))
        assert analytics.conn is not None
        analytics.close()

    def test_list_jobs(self):
        """list_jobs 应返回 jobs 表数据。"""
        ResearchDatabase(db_path=self.db_path).create_job("2026-08-11", ["sh.600519"])

        analytics = ResearchAnalytics(self.db_path, ParquetPaths(self.parquet_root))
        df = analytics.list_jobs()
        assert len(df) == 1
        assert df.iloc[0]["date"] == "2026-08-11"
        analytics.close()

    def test_get_signals_by_job(self):
        """get_signals_by_job 应返回 signals 表数据。"""
        db = ResearchDatabase(db_path=self.db_path)
        job_id = db.create_job("2026-08-11", ["sh.600519", "sz.000858"])
        db.insert_signals(
            job_id,
            [
                {
                    "ticker": "sh.600519",
                    "rank": 1,
                    "composite_score": 85.0,
                    "ta_signal": "BUY",
                    "ta_confidence": 80.0,
                    "kronos_direction": "UP",
                    "kronos_change_pct": 3.2,
                    "ta_reasoning": "",
                    "uncertainty": None,
                    "ta_error": None,
                    "kronos_error": None,
                },
                {
                    "ticker": "sz.000858",
                    "rank": 2,
                    "composite_score": 72.0,
                    "ta_signal": "HOLD",
                    "ta_confidence": 60.0,
                    "kronos_direction": "DOWN",
                    "kronos_change_pct": -1.5,
                    "ta_reasoning": "",
                    "uncertainty": None,
                    "ta_error": None,
                    "kronos_error": None,
                },
            ],
        )
        db.complete_job(job_id, n_success=2, elapsed=5.0)

        analytics = ResearchAnalytics(self.db_path, ParquetPaths(self.parquet_root))
        df = analytics.get_signals_by_job(job_id)
        assert len(df) == 2
        assert df.iloc[0]["ticker"] == "sh.600519"
        assert df.iloc[0]["rank"] == 1
        analytics.close()

    def test_cross_sectional_ic(self):
        """cross_sectional_ic 应计算 Spearman IC。"""
        db = ResearchDatabase(db_path=self.db_path)
        job_id = db.create_job("2026-08-11", ["sh.600519", "sz.000858", "sh.600036"])

        # 插入 signals + ta_analysis（含 return）
        db.insert_ta(
            job_id,
            MagicMock(
                ticker="sh.600519",
                error=None,
                elapsed_sec=1.0,
                llm_request=None,
                signal="BUY",
                confidence=80.0,
                investment_decision=MagicMock(thesis="test", risks=[]),
            ),
        )
        db.insert_ta(
            job_id,
            MagicMock(
                ticker="sz.000858",
                error=None,
                elapsed_sec=1.0,
                llm_request=None,
                signal="HOLD",
                confidence=60.0,
                investment_decision=MagicMock(thesis="test", risks=[]),
            ),
        )
        db.insert_ta(
            job_id,
            MagicMock(
                ticker="sh.600036",
                error=None,
                elapsed_sec=1.0,
                llm_request=None,
                signal="SELL",
                confidence=40.0,
                investment_decision=MagicMock(thesis="test", risks=[]),
            ),
        )

        db.insert_signals(
            job_id,
            [
                {
                    "ticker": "sh.600519",
                    "rank": 1,
                    "composite_score": 85.0,
                    "ta_signal": "BUY",
                    "ta_confidence": 80.0,
                    "ta_reasoning": "",
                    "uncertainty": None,
                    "ta_error": None,
                    "kronos_error": None,
                    "actual_return_pct": 2.5,
                },
                {
                    "ticker": "sz.000858",
                    "rank": 2,
                    "composite_score": 72.0,
                    "ta_signal": "HOLD",
                    "ta_confidence": 60.0,
                    "ta_reasoning": "",
                    "uncertainty": None,
                    "ta_error": None,
                    "kronos_error": None,
                    "actual_return_pct": -0.5,
                },
                {
                    "ticker": "sh.600036",
                    "rank": 3,
                    "composite_score": 60.0,
                    "ta_signal": "SELL",
                    "ta_confidence": 40.0,
                    "ta_reasoning": "",
                    "uncertainty": None,
                    "ta_error": None,
                    "kronos_error": None,
                    "actual_return_pct": -1.0,
                },
            ],
        )

        analytics = ResearchAnalytics(self.db_path, ParquetPaths(self.parquet_root))
        df = analytics.cross_sectional_ic(job_id)
        assert len(df) == 1
        assert df.iloc[0]["n_stocks"] == 3
        # 正相关：高评分 → 高收益
        ic = df.iloc[0]["spearman_ic"]
        assert ic is not None and ic > 0
        analytics.close()

    def test_query_features(self):
        """query_features 应读取 Parquet 特征文件。"""
        writer = ParquetWriter(ParquetPaths(self.parquet_root))

        # 写入特征文件
        path = writer.write_feature(
            "sh.600519",
            "2026-08-11",
            {
                "ticker": "sh.600519",
                "eval_date": "2026-08-11",
                "signal": "BUY",
                "confidence": 80.0,
            },
        )
        assert path.exists()

        analytics = ResearchAnalytics(self.db_path, ParquetPaths(self.parquet_root))
        df = analytics.query_features(tickers=["sh.600519"])
        assert len(df) == 1
        assert df.iloc[0]["signal"] == "BUY"
        analytics.close()

    def test_describe_stats(self):
        """describe 应返回各数据源的计数。"""
        ResearchDatabase(db_path=self.db_path).create_job("2026-08-11", ["sh.600519"])

        analytics = ResearchAnalytics(self.db_path, ParquetPaths(self.parquet_root))
        stats = analytics.describe()
        assert stats["jobs"] == 1
        analytics.close()

    def test_close_twice_safe(self):
        """多次 close 不应报错。"""
        analytics = ResearchAnalytics(self.db_path, ParquetPaths(self.parquet_root))
        analytics.close()
        analytics.close()  # 不应抛异常


# ══════════════════════════════════════════════════════════════════════════════
#  ParquetPaths / ParquetWriter 测试（不依赖 DuckDB）
# ══════════════════════════════════════════════════════════════════════════════


class TestParquetPaths:
    def test_feature_path_structure(self, tmp_path):
        paths = ParquetPaths(tmp_path / "data")
        p = paths.feature_path("sh.600519", "2026-08-11")
        assert p.suffix == ".parquet"
        assert "features" in str(p)
        assert "2026" in str(p)
        assert "08" in str(p)
        assert "sh_600519_2026-08-11.parquet" == p.name

    def test_prediction_path_structure(self, tmp_path):
        paths = ParquetPaths(tmp_path / "data")
        p = paths.prediction_path("sh.600519", "2026-08-11", 30)
        assert "sh_600519_2026-08-11_30.parquet" == p.name

    def test_backtest_path_structure(self, tmp_path):
        paths = ParquetPaths(tmp_path / "data")
        p = paths.backtest_path("job-abc-123")
        assert "sh_600519_2026-08-11_30.parquet" not in str(p)
        assert "job-abc-123.parquet" == p.name


class TestParquetWriter:
    def test_write_feature(self, tmp_path):
        paths = ParquetPaths(tmp_path / "data")
        writer = ParquetWriter(paths)
        path = writer.write_feature(
            "sh.600519",
            "2026-08-11",
            {
                "ticker": "sh.600519",
                "eval_date": "2026-08-11",
                "signal": "BUY",
                "confidence": 85.0,
            },
        )
        assert path.exists()
        import pandas as pd

        df = pd.read_parquet(path)
        assert len(df) == 1
        assert df.iloc[0]["signal"] == "BUY"

    def test_write_prediction(self, tmp_path):
        paths = ParquetPaths(tmp_path / "data")
        writer = ParquetWriter(paths)
        path = writer.write_prediction(
            "sh.600519",
            "2026-08-11",
            30,
            {
                "ticker": "sh.600519",
                "eval_date": "2026-08-11",
                "direction": "UP",
                "expected_change_pct": 2.5,
            },
        )
        assert path.exists()
        import pandas as pd

        df = pd.read_parquet(path)
        assert len(df) == 1
        assert df.iloc[0]["direction"] == "UP"

    def test_write_backtest(self, tmp_path):
        paths = ParquetPaths(tmp_path / "data")
        writer = ParquetWriter(paths)
        records = [
            {
                "ticker": "sh.600519",
                "action": "BUY",
                "return_pct": 2.5,
                "entry_date": "2026-08-11",
                "exit_date": "2026-08-16",
            },
            {
                "ticker": "sz.000858",
                "action": "HOLD",
                "return_pct": -0.3,
                "entry_date": "2026-08-11",
                "exit_date": "2026-08-16",
            },
        ]
        path = writer.write_backtest("job-001", records)
        assert path.exists()
        import pandas as pd

        df = pd.read_parquet(path)
        assert len(df) == 2
        assert df.iloc[0]["ticker"] == "sh.600519"


# ══════════════════════════════════════════════════════════════════════════════
#  DuckDB 不可用时降级行为
# ══════════════════════════════════════════════════════════════════════════════


class TestDuckdbFallback:
    def test_get_analytics_returns_none_when_no_duckdb(self):
        """DuckDB 不可用时 get_analytics 应返回 None。"""
        with patch("trade_krono_cli.analytics_db._HAS_DUCKDB", False):
            result = get_analytics()
            assert result is None

    def test_research_analytics_raises_when_no_duckdb(self):
        """直接创建 ResearchAnalytics 时，无 DuckDB 应抛 RuntimeError。"""
        with patch("trade_krono_cli.analytics_db._HAS_DUCKDB", False):
            with pytest.raises(RuntimeError, match="DuckDB 未安装"):
                ResearchAnalytics(
                    Path("/tmp/fake.db"),
                    ParquetPaths(Path("/tmp/fake_data")),
                )
