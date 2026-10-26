"""Tests for scripts._utils helpers."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from unittest.mock import patch

import pandas as pd
import pytest

if TYPE_CHECKING:
    from pathlib import Path


class TestGetExpectedDate:
    """_get_expected_date: 工作日→昨天，周末→最近周五。"""

    def test_weekday_returns_yesterday(self) -> None:
        """工作日：返回昨天日期。"""
        from scripts._utils import _get_expected_date

        # 用周三作基准，避免周末回退逻辑干扰
        wednesday = datetime(2026, 9, 16)  # 周三 weekday=2
        with patch("scripts._utils.datetime") as mock_dt:
            mock_dt.now.return_value = wednesday
            mock_dt.timedelta = timedelta
            result = _get_expected_date()
        assert result == "2026-09-15"

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

        with (
            patch("scripts._utils._get_expected_date", return_value="2026-09-23"),
            patch("scripts._utils._get_cache_latest_date", return_value="2026-09-24"),
        ):
            result = check_data_freshness()
        assert result.startswith("✅ 缓存已更新至")
        assert "2026-09-24" in result

    def test_stale_triggers_sync(self) -> None:
        from scripts._utils import check_data_freshness

        with (
            patch("scripts._utils._get_expected_date", return_value="2026-09-24"),
            patch(
                "scripts._utils._get_cache_latest_date", side_effect=["2026-09-20", "2026-09-24"]
            ),
            patch("scripts._utils._trigger_sync", return_value=True),
        ):
            result = check_data_freshness()
        assert "同步完成" in result

    def test_sync_failure_returns_warning(self) -> None:
        from scripts._utils import check_data_freshness

        with (
            patch("scripts._utils._get_expected_date", return_value="2026-09-24"),
            patch("scripts._utils._get_cache_latest_date", return_value="2026-09-20"),
            patch("scripts._utils._trigger_sync", return_value=False),
        ):
            result = check_data_freshness()
        assert "缓存较旧" in result
        assert "已尝试同步但失败" in result

    def test_empty_cache_triggers_sync(self) -> None:
        from scripts._utils import check_data_freshness

        with (
            patch("scripts._utils._get_expected_date", return_value="2026-09-24"),
            patch("scripts._utils._get_cache_latest_date", side_effect=[None, "2026-09-24"]),
            patch("scripts._utils._trigger_sync", return_value=True),
        ):
            result = check_data_freshness()
        assert "✅" in result

    def test_custom_logger(self) -> None:
        from scripts._utils import check_data_freshness

        messages: list[str] = []

        def log(msg: str) -> None:
            messages.append(msg)

        with (
            patch("scripts._utils._get_expected_date", return_value="2026-09-24"),
            patch("scripts._utils._get_cache_latest_date", return_value="2026-09-23"),
            patch("scripts._utils._trigger_sync", return_value=False),
        ):
            check_data_freshness(logger_fn=log)
        assert len(messages) == 2
        assert "缓存较旧" in messages[0]
        assert "同步失败" in messages[1]


class TestCheckKlineRegime:
    """check_kline_regime: 复权基准漂移检测。"""

    def _df(self, closes: list[float]) -> pd.DataFrame:

        n = len(closes)
        return pd.DataFrame(
            {
                "timestamps": pd.date_range("2026-01-01", periods=n, freq="B"),
                "close": closes,
            }
        )

    def test_normal_kline_passes(self) -> None:
        from scripts._utils import check_kline_regime

        # 正常 100 天，价格 100→105 缓升
        closes = [100.0 + i * 0.05 for i in range(100)]
        check_kline_regime("sh.600519", self._df(closes))  # 不抛异常

    def test_regime_drift_raises(self) -> None:
        import pytest

        from scripts._utils import check_kline_regime
        from trade_krono_cli.errors import DataInconsistencyError

        # 前 99 天 ~300（旧复权体系），最后 1 天 17.4（新复权体系，-94%）
        closes = [300.0] * 99 + [17.4]
        with pytest.raises(DataInconsistencyError, match="复权基准漂移"):
            check_kline_regime("sh.600674", self._df(closes))

    def test_30pct_exright_passes(self) -> None:
        """真实除权日 ~30% 跌幅不应触发（阈值 50% 之上才判漂移）。"""
        from scripts._utils import check_kline_regime

        closes = [100.0] * 99 + [70.0]  # -30%
        check_kline_regime("sz.000001", self._df(closes))  # 不抛

    def test_single_day_skips(self) -> None:
        """仅 1 日数据直接跳过（无相邻比较）。"""
        from scripts._utils import check_kline_regime

        check_kline_regime("sh.600000", self._df([100.0]))

    def test_none_kline_passes(self) -> None:
        from scripts._utils import check_kline_regime

        check_kline_regime("sh.600000", None)  # type: ignore[arg-type]

    def test_empty_close_passes(self) -> None:
        import pandas as pd

        df = pd.DataFrame({"timestamps": pd.date_range("2026-01-01", periods=5), "close": [None] * 5})
        from scripts._utils import check_kline_regime

        check_kline_regime("sh.600000", df)
