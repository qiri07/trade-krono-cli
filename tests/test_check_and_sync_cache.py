#!/usr/bin/env python3
"""scripts.check_and_sync_cache 单元测试。"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

# 确保脚本路径可导入
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from typing import TYPE_CHECKING

from check_and_sync_cache import (  # noqa: E402
    get_cache_latest_date,
    get_expected_date,
    is_cache_up_to_date,
    main,
    run_sync,
    run_sync_with_fallback,
)

if TYPE_CHECKING:
    import pytest


class TestGetExpectedDate:
    """测试 get_expected_date 函数。"""

    def test_weekday_returns_yesterday(self) -> None:
        """工作日返回昨天。"""
        with patch("check_and_sync_cache.datetime") as mock_dt:
            # 模拟今天是周三 2026-09-09
            today = datetime(2026, 9, 9)  # 周三
            mock_dt.now.return_value = today
            mock_dt.timedelta = timedelta

            result = get_expected_date()
            assert result == "2026-09-08"  # 昨天是周二

    def test_saturday_returns_friday(self) -> None:
        """周六时返回周五。"""
        with patch("check_and_sync_cache.datetime") as mock_dt:
            # 2026-09-05 是周六
            today = datetime(2026, 9, 5)  # 周六
            mock_dt.now.return_value = today
            mock_dt.timedelta = timedelta

            result = get_expected_date()
            assert result == "2026-09-04"  # 周五

    def test_sunday_returns_friday(self) -> None:
        """周日时返回周五。"""
        with patch("check_and_sync_cache.datetime") as mock_dt:
            # 2026-09-06 是周日
            today = datetime(2026, 9, 6)  # 周日
            mock_dt.now.return_value = today
            mock_dt.timedelta = timedelta

            result = get_expected_date()
            assert result == "2026-09-04"  # 周五


class TestGetCacheLatestDate:
    """测试 get_cache_latest_date 函数。"""

    def test_returns_none_when_db_missing(self, tmp_path: Path) -> None:
        """数据库不存在时返回 None。"""
        with patch("check_and_sync_cache.CACHE_DB", tmp_path / "nonexistent.db"):
            result = get_cache_latest_date()
            assert result is None

    def test_returns_latest_date(self, tmp_path: Path) -> None:
        """返回缓存中的最新日期。"""
        db = tmp_path / "test.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE kline_cache (ticker TEXT, start TEXT, end TEXT, data BLOB)")
        conn.execute(
            "INSERT INTO kline_cache VALUES ('sh.600519', '2022-01-01', '2026-09-07', ?)",
            (b"data",),
        )
        conn.execute(
            "INSERT INTO kline_cache VALUES ('sh.600000', '2022-01-01', '2026-09-06', ?)",
            (b"data",),
        )
        conn.commit()
        conn.close()

        with patch("check_and_sync_cache.CACHE_DB", db):
            result = get_cache_latest_date()
            assert result == "2026-09-07"

    def test_returns_none_on_exception(self, tmp_path: Path) -> None:
        """查询异常时返回 None。"""
        db = tmp_path / "test.db"
        db.write_bytes(b"invalid sqlite")

        with patch("check_and_sync_cache.CACHE_DB", db):
            result = get_cache_latest_date()
            assert result is None


class TestIsCacheUpToDate:
    """测试 is_cache_up_to_date 函数。"""

    def test_returns_false_when_no_cache(self) -> None:
        """无缓存时返回 False。"""
        with patch("check_and_sync_cache.get_cache_latest_date", return_value=None):
            assert is_cache_up_to_date("2026-09-07") is False

    def test_returns_true_when_up_to_date(self, tmp_path: Path) -> None:
        """缓存已更新时返回 True。"""
        db = tmp_path / "test.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE kline_cache (ticker TEXT, start TEXT, end TEXT, data BLOB)")
        conn.execute(
            "INSERT INTO kline_cache VALUES ('sh.600519', '2022-01-01', '2026-09-08', ?)",
            (b"data",),
        )
        conn.commit()
        conn.close()

        with patch("check_and_sync_cache.CACHE_DB", db):
            assert is_cache_up_to_date("2026-09-07") is True

    def test_returns_false_when_behind(self, tmp_path: Path) -> None:
        """缓存滞后时返回 False。"""
        db = tmp_path / "test.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE kline_cache (ticker TEXT, start TEXT, end TEXT, data BLOB)")
        conn.execute(
            "INSERT INTO kline_cache VALUES ('sh.600519', '2022-01-01', '2026-09-04', ?)",
            (b"data",),
        )
        conn.commit()
        conn.close()

        with patch("check_and_sync_cache.CACHE_DB", db):
            assert is_cache_up_to_date("2026-09-07") is False


class TestRunSync:
    """测试 run_sync 函数。"""

    def test_dry_run_returns_true(self) -> None:
        """dry_run 模式直接返回 True。"""
        result = run_sync(dry_run=True)
        assert result is True

    @patch("scripts.check_and_sync_cache.subprocess.run")
    def test_sync_success(self, mock_run: MagicMock) -> None:
        """同步成功时返回 True。"""
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = ""

        result = run_sync(source="mootdx", dry_run=False)
        assert result is True

    @patch("scripts.check_and_sync_cache.subprocess.run")
    def test_sync_failure(self, mock_run: MagicMock) -> None:
        """同步失败时返回 False。"""
        mock_run.return_value.returncode = 1
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = "error"

        result = run_sync(source="mootdx", dry_run=False)
        assert result is False


class TestRunSyncWithFallback:
    """测试 run_sync_with_fallback 函数。"""

    @patch("check_and_sync_cache.run_sync")
    def test_first_source_succeeds(self, mock_run_sync: MagicMock) -> None:
        """第一个源成功时返回 True。"""
        mock_run_sync.return_value = True

        result = run_sync_with_fallback(dry_run=True)
        assert result is True
        mock_run_sync.assert_called_once_with(source="tonghuashun", dry_run=True)

    @patch("check_and_sync_cache.run_sync")
    def test_all_sources_fail(self, mock_run_sync: MagicMock) -> None:
        """所有源失败时返回 False。"""
        mock_run_sync.return_value = False

        result = run_sync_with_fallback(dry_run=True)
        assert result is False
        assert mock_run_sync.call_count == 3


class TestMain:
    """测试 main 函数。"""

    def test_dry_run_returns_zero(self, capsys: pytest.CaptureFixture[str]) -> None:
        """dry-run 模式返回 0。"""
        with patch("sys.argv", ["check_and_sync_cache.py", "--dry-run"]):
            result = main()
            assert result == 0

    def test_force_sync_flag(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--force-sync 标志解析正确。"""
        parser = argparse.ArgumentParser()
        parser.add_argument("--force-sync", action="store_true")
        args = parser.parse_args(["--force-sync"])
        assert args.force_sync is True

    def test_source_choice(self) -> None:
        """--source 参数选择正确。"""
        valid_sources = ["mootdx", "akshare", "tonghuashun"]
        for source in valid_sources:
            parser = argparse.ArgumentParser()
            parser.add_argument("--source", default="tonghuashun", choices=valid_sources)
            args = parser.parse_args(["--source", source])
            assert args.source == source


class TestGetStaleTickers:
    """测试逐票滞后检测 get_stale_tickers。"""

    @staticmethod
    def _build_db(path: Path) -> None:
        conn = sqlite3.connect(str(path))
        conn.execute(
            "CREATE TABLE kline_cache (ticker TEXT, start TEXT, end TEXT, data BLOB)"
        )
        rows = [
            ("sh.600519", "2026-11-10"),
            ("sz.000001", "2026-11-09"),   # A 股滞后
            ("sh.510300", "2026-11-09"),   # ETF 滞后
            ("bj.920080", "2026-11-06"),   # 北交所滞后
            ("hk.00700", "2026-11-09"),    # 港股滞后
        ]
        conn.executemany(
            "INSERT INTO kline_cache VALUES (?, '2020-01-01', ?, NULL)", rows
        )
        conn.commit()
        conn.close()

    def test_groups_stale_by_category(self, tmp_path: Path) -> None:
        from check_and_sync_cache import get_stale_tickers

        db = tmp_path / "cache.db"
        self._build_db(db)
        with patch("check_and_sync_cache.CACHE_DB", db):
            stale = get_stale_tickers("2026-11-10")
        assert stale["A股"] == ["sz.000001"]
        assert stale["ETF"] == ["sh.510300"]
        assert stale["北交所"] == ["bj.920080"]
        assert stale["港股"] == ["hk.00700"]
        assert "sh.600519" not in sum(stale.values(), [])

    def test_returns_empty_when_all_fresh(self, tmp_path: Path) -> None:
        from check_and_sync_cache import get_stale_tickers

        db = tmp_path / "cache.db"
        self._build_db(db)
        with patch("check_and_sync_cache.CACHE_DB", db):
            stale = get_stale_tickers("2026-11-01")
        assert stale == {}

    def test_missing_db_returns_empty(self, tmp_path: Path) -> None:
        from check_and_sync_cache import get_stale_tickers

        with patch("check_and_sync_cache.CACHE_DB", tmp_path / "none.db"):
            assert get_stale_tickers("2026-11-10") == {}


class TestMainStaleGate:
    """全局最新但存在滞后标的时，main 不再直接返回「无需同步」。"""

    def test_logs_warning_when_stale_exists(self) -> None:
        with (
            patch("sys.argv", ["check_and_sync_cache.py"]),
            patch("check_and_sync_cache.get_expected_date", return_value="2026-11-10"),
            patch("check_and_sync_cache.get_cache_latest_date", return_value="2026-11-10"),
            patch(
                "check_and_sync_cache.get_stale_tickers",
                return_value={"ETF": ["sh.510300"], "北交所": ["bj.920080"]},
            ),
            patch("check_and_sync_cache.run_sync_with_fallback") as mock_sync,
            patch("check_and_sync_cache.logger") as mock_logger,
        ):
            assert main() == 0
        mock_sync.assert_not_called()
        assert any(
            "未更新至" in str(call.args[0]) for call in mock_logger.warning.call_args_list
        )

    def test_force_sync_triggers_targeted_repair(self) -> None:
        with (
            patch("sys.argv", ["check_and_sync_cache.py", "--force-sync"]),
            patch("check_and_sync_cache.get_expected_date", return_value="2026-11-10"),
            patch("check_and_sync_cache.get_cache_latest_date", return_value="2026-11-10"),
            patch(
                "check_and_sync_cache.get_stale_tickers",
                return_value={"A股": ["sz.000001"]},
            ),
            patch(
                "check_and_sync_cache.run_targeted_repair", return_value=True
            ) as mock_repair,
        ):
            assert main() == 0
        mock_repair.assert_called_once()
        assert mock_repair.call_args.args[0] == ["sz.000001"]
