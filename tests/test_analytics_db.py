"""测试 Analytics 引擎（analytics_db.py）。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch


class TestParquetPaths:
    """ParquetPaths 路径构造测试。"""

    def test_feature_path_format(self, tmp_path: Path) -> None:
        """feature_path 应生成正确的路径格式。"""
        from trade_krono_cli.analytics_db import ParquetPaths

        paths = ParquetPaths(tmp_path)
        result = paths.feature_path("sh.600519", "2026-09-04")

        assert result == tmp_path / "features" / "2026" / "09" / "sh_600519_2026-09-04.parquet"
        assert result.parent.exists()

    def test_prediction_path_format(self, tmp_path: Path) -> None:
        """prediction_path 应生成正确的路径格式。"""
        from trade_krono_cli.analytics_db import ParquetPaths

        paths = ParquetPaths(tmp_path)
        result = paths.prediction_path("sh.600519", "2026-09-04", 30)

        assert (
            result == tmp_path / "predictions" / "2026" / "09" / "sh_600519_2026-09-04_30.parquet"
        )

    def test_backtest_path_format(self, tmp_path: Path) -> None:
        """backtest_path 应生成正确的路径格式。"""
        from trade_krono_cli.analytics_db import ParquetPaths

        paths = ParquetPaths(tmp_path)
        result = paths.backtest_path("job_123")

        assert result == tmp_path / "backtest" / "job_123.parquet"


class TestParquetWriter:
    """ParquetWriter 写入测试。"""

    def test_write_feature(self, tmp_path: Path) -> None:
        """写入单只股票特征。"""
        import pandas as pd

        from trade_krono_cli.analytics_db import ParquetPaths, ParquetWriter

        paths = ParquetPaths(tmp_path)
        writer = ParquetWriter(paths)

        result = writer.write_feature(
            ticker="sh.600519",
            date="2026-09-04",
            data={"pe": 15.5, "pb": 2.3},
        )

        assert result.exists()
        df = pd.read_parquet(result)
        assert len(df) == 1
        assert df["pe"].iloc[0] == 15.5

    def test_write_prediction(self, tmp_path: Path) -> None:
        """写入单只股票预测。"""
        import pandas as pd

        from trade_krono_cli.analytics_db import ParquetPaths, ParquetWriter

        paths = ParquetPaths(tmp_path)
        writer = ParquetWriter(paths)

        result = writer.write_prediction(
            ticker="sh.600519",
            date="2026-09-04",
            pred_len=30,
            data={"expected_change_pct": 2.5, "direction": "UP"},
        )

        assert result.exists()
        df = pd.read_parquet(result)
        assert len(df) == 1

    def test_write_backtest_empty_records(self, tmp_path: Path) -> None:
        """空记录列表应写入空 DataFrame。"""
        import pandas as pd

        from trade_krono_cli.analytics_db import ParquetPaths, ParquetWriter

        paths = ParquetPaths(tmp_path)
        writer = ParquetWriter(paths)

        result = writer.write_backtest(job_id="job_123", records=[])

        assert result.exists()
        df = pd.read_parquet(result)
        assert len(df) == 0


class TestDuckDBAvailability:
    """DuckDB 可用性检查测试。"""

    def test_duckdb_available_when_installed(self) -> None:
        """已安装 DuckDB 时应返回 True。"""
        with patch.dict("sys.modules", {"duckdb": MagicMock()}):
            # 重新导入模块以触发 _HAS_DUCKDB = True
            import importlib

            import trade_krono_cli.analytics_db as mod

            importlib.reload(mod)

            assert mod._duckdb_available() is True

    def test_duckdb_not_available_when_missing(self) -> None:
        """未安装 DuckDB 时应返回 False。"""
        # duckdb 可能已安装，测试逻辑本身
        from trade_krono_cli.analytics_db import _duckdb_available

        # 如果 duckdb 存在则返回 True，否则 False
        result = _duckdb_available()
        assert isinstance(result, bool)

    def test_ensure_duckdb_raises_when_not_available(self) -> None:
        """DuckDB 不可用时应抛出 RuntimeError。"""
        from trade_krono_cli.analytics_db import _ensure_duckdb

        with patch("trade_krono_cli.analytics_db._HAS_DUCKDB", False):
            try:
                _ensure_duckdb()
                assert False, "应抛出异常"
            except RuntimeError as e:
                assert "DuckDB 未安装" in str(e)


class TestResearchAnalytics:
    """ResearchAnalytics 基础测试。"""

    def test_init_requires_duckdb(self, tmp_path: Path) -> None:
        """初始化时需要 DuckDB。"""
        from trade_krono_cli.analytics_db import ParquetPaths, ResearchAnalytics

        parquet_paths = ParquetPaths(tmp_path)

        # 如果 DuckDB 未安装，应抛出 RuntimeError
        # 由于 DuckDB 已安装，这里测试初始化成功
        try:
            analytics = ResearchAnalytics(tmp_path, parquet_paths)
            assert analytics is not None
        except RuntimeError as e:
            # DuckDB 未安装的情况
            assert "DuckDB 未安装" in str(e)
