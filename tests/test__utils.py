"""Tests for scripts._utils helpers."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest


class TestGetExpectedDate:
    """_get_expected_date: 工作日→昨天，周末→最近周五。"""

    def test_weekday_returns_yesterday(self) -> None:
        """工作日：返回昨天日期。"""
        from scripts._utils import _get_expected_date

        today = datetime.now()
        expected = (today - timedelta(days=1)).strftime("%Y-%m-%d")
        assert _get_expected_date() == expected

    @pytest.mark.parametrize("weekday", [5, 6])
    def test_weekend_returns_last_friday(self, weekday: int) -> None:
        """周六/周日：回退到最近周五。"""
        from scripts._utils import _get_expected_date

        # 构造一个指定星期几的"今天"
        today = datetime(2026, 9, 13)  # 周日 weekday=6
        with patch("scripts._utils.datetime") as mock_dt:
            mock_dt.now.return_value = today
            mock_dt.timedelta = timedelta
            result = _get_expected_date()
        # 周五是 2026-09-11
        assert result == "2026-09-11"


class TestGetCacheLatestDate:
    """_get_cache_latest_date: 数据库查询最新 end 日期。"""

    def test_db_not_exists(self, tmp_path: Path) -> None:
        from scripts._utils import _get_cache_latest_date

        with patch("scripts._utils.CACHE_DB", tmp_path / "nonexistent.db"):
            assert _get_cache_latest_date() is None

    def test_returns_latest_end(self, tmp_path: Path) -> None:
        from scripts._utils import _get_cache_latest_date

        db = tmp_path / "cache.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE kline_cache (ticker TEXT, end TEXT, data BLOB)")
        conn.execute("INSERT INTO kline_cache VALUES ('sh.600519', '2026-09-23', X'DEADBEEF')")
        conn.execute("INSERT INTO kline_cache VALUES ('sz.000858', '2026-09-24', X'CAFEBABE')")
        conn.execute("INSERT INTO kline_cache VALUES ('sh.600036', '2026-09-22', X'FACE')")
        conn.commit()
        conn.close()

        with patch("scripts._utils.CACHE_DB", db):
            assert _get_cache_latest_date() == "2026-09-24"

    def test_db_corrupt_returns_none(self, tmp_path: Path) -> None:
        from scripts._utils import _get_cache_latest_date

        db = tmp_path / "corrupt.db"
        db.write_bytes(b"not a db")

        with patch("scripts._utils.CACHE_DB", db):
            assert _get_cache_latest_date() is None


class TestCheckDataFreshness:
    """check_data_freshness: 日期比较 + 触发同步逻辑。"""

    def test_already_fresh(self) -> None:
        from scripts._utils import check_data_freshness

        with patch("scripts._utils._get_expected_date", return_value="2026-09-23"), \
             patch("scripts._utils._get_cache_latest_date", return_value="2026-09-24"):
            result = check_data_freshness()
        assert result.startswith("✅ 缓存已更新至")
        assert "2026-09-24" in result

    def test_stale_triggers_sync(self) -> None:
        from scripts._utils import check_data_freshness

        with patch("scripts._utils._get_expected_date", return_value="2026-09-24"), \
             patch("scripts._utils._get_cache_latest_date", side_effect=["2026-09-20", "2026-09-24"]), \
             patch("scripts._utils._trigger_sync", return_value=True):
            result = check_data_freshness()
        assert "同步完成" in result

    def test_sync_failure_returns_warning(self) -> None:
        from scripts._utils import check_data_freshness

        with patch("scripts._utils._get_expected_date", return_value="2026-09-24"), \
             patch("scripts._utils._get_cache_latest_date", return_value="2026-09-20"), \
             patch("scripts._utils._trigger_sync", return_value=False):
            result = check_data_freshness()
        assert "缓存较旧" in result
        assert "已尝试同步但失败" in result

    def test_empty_cache_triggers_sync(self) -> None:
        from scripts._utils import check_data_freshness

        with patch("scripts._utils._get_expected_date", return_value="2026-09-24"), \
             patch("scripts._utils._get_cache_latest_date", side_effect=[None, "2026-09-24"]), \
             patch("scripts._utils._trigger_sync", return_value=True):
            result = check_data_freshness()
        assert "✅" in result

    def test_custom_logger(self) -> None:
        from scripts._utils import check_data_freshness

        messages: list[str] = []
        def log(msg: str) -> None:
            messages.append(msg)

        with patch("scripts._utils._get_expected_date", return_value="2026-09-24"), \
             patch("scripts._utils._get_cache_latest_date", return_value="2026-09-23"):
            check_data_freshness(logger_fn=log)
        assert len(messages) == 1
        assert "缓存较旧" in messages[0]
