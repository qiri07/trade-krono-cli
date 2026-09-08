"""测试 trade_krono_cli.utils.helpers — 共享工具函数。"""

from __future__ import annotations

import pandas as pd
import pytest

from trade_krono_cli.utils.helpers import next_business_days, validate_data_freshness


class TestNextBusinessDays:
    """next_business_days 测试。"""

    def test_returns_n_days(self) -> None:
        days = next_business_days("2026-09-01", 3)
        assert len(days) == 3
        assert all(isinstance(d, pd.Timestamp) for d in days)

    def test_skips_weekends(self) -> None:
        """起始为周五，下一个工作日应为下周一。"""
        days = next_business_days("2026-09-04", 2)  # 2026-09-04 is Friday
        assert days[0].weekday() == 0  # Monday
        assert days[1].weekday() == 1  # Tuesday

    def test_single_day(self) -> None:
        days = next_business_days("2026-09-01", 1)
        assert len(days) == 1
        assert days[0] > pd.Timestamp("2026-09-01")

    def test_zero_days_empty_list(self) -> None:
        days = next_business_days("2026-09-01", 0)
        assert days == []


class TestValidateDataFreshness:
    """validate_data_freshness 测试。"""

    def test_valid_data_no_error(self) -> None:
        df = pd.DataFrame(
            {
                "timestamps": pd.date_range("2026-08-20", periods=5, freq="D"),
                "open": [10.0] * 5,
                "close": [10.5] * 5,
            }
        )
        # 不抛异常
        validate_data_freshness(df, "2026-08-27", "600519", max_gap_trading_days=10)

    def test_missing_timestamps_column_raises(self) -> None:
        df = pd.DataFrame({"open": [10.0], "close": [10.5]})
        with pytest.raises(RuntimeError, match="缺少 timestamps 列"):
            validate_data_freshness(df, "2026-08-27", "600519")

    def test_future_data_raises(self) -> None:
        """数据截止日晚于评估日期时应报未来化错误。"""
        df = pd.DataFrame(
            {
                "timestamps": pd.date_range("2026-09-01", periods=3, freq="D"),
                "open": [10.0] * 3,
                "close": [10.5] * 3,
            }
        )
        with pytest.raises(RuntimeError, match="数据未来化"):
            validate_data_freshness(df, "2026-08-27", "600519")

    def test_stale_data_raises(self) -> None:
        """数据间隔超过阈值时应报过旧错误。"""
        df = pd.DataFrame(
            {
                "timestamps": pd.date_range("2026-07-01", periods=10, freq="D"),
                "open": [10.0] * 10,
                "close": [10.5] * 10,
            }
        )
        with pytest.raises(RuntimeError, match="数据过旧"):
            validate_data_freshness(df, "2026-08-27", "600519", max_gap_trading_days=5)

    def test_edge_case_exact_threshold_allowed(self) -> None:
        """恰好等于阈值时不报错。"""
        df = pd.DataFrame(
            {
                "timestamps": pd.date_range("2026-08-20", periods=5, freq="B"),
                "open": [10.0] * 5,
                "close": [10.5] * 5,
            }
        )
        # 不抛异常
        validate_data_freshness(df, "2026-08-29", "600519", max_gap_trading_days=5)
