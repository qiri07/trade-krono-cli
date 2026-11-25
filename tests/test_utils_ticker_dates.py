"""trade_krono_cli.utils 剩余工具函数单元测试。

覆盖 safe_float、strip_ticker_prefix、add_ticker_prefix、next_business_days、
validate_data_freshness。test_utils.py 已覆盖 parse_range/parse_comma_list/
parse_float/merge_with_nested，此处不重复。
"""

from __future__ import annotations

import pandas as pd

from trade_krono_cli.utils import (
    add_ticker_prefix,
    safe_float,
    strip_ticker_prefix,
)
from trade_krono_cli.utils.helpers import (
    next_business_days,
    validate_data_freshness,
)


class TestSafeFloat:
    def test_valid(self) -> None:
        assert safe_float("3.14") == 3.14
        assert safe_float("42") == 42.0

    def test_none_and_invalid(self) -> None:
        assert safe_float(None) is None
        assert safe_float("") is None
        assert safe_float("abc") is None
        assert safe_float("--") is None

    def test_nan_inf(self) -> None:
        assert safe_float(float("nan")) is None
        assert safe_float(float("inf")) is None
        assert safe_float(float("-inf")) is None


class TestStripTickerPrefix:
    def test_strips_all_prefixes(self) -> None:
        assert strip_ticker_prefix("sh.600519") == "600519"
        assert strip_ticker_prefix("sz.000858") == "000858"
        assert strip_ticker_prefix("bj.920001") == "920001"
        assert strip_ticker_prefix("SH.600519") == "600519"
        assert strip_ticker_prefix("SZ.000858") == "000858"
        assert strip_ticker_prefix("BJ.920001") == "920001"

    def test_no_prefix_unchanged(self) -> None:
        assert strip_ticker_prefix("600519") == "600519"


class TestAddTickerPrefix:
    def test_maps_by_leading_digit(self) -> None:
        assert add_ticker_prefix("600519") == "sh.600519"
        assert add_ticker_prefix("510300") == "sh.510300"  # ETF 沪市
        assert add_ticker_prefix("000858") == "sz.000858"
        assert add_ticker_prefix("159915") == "sz.159915"  # ETF 深市
        assert add_ticker_prefix("920001") == "bj.920001"

    def test_preserves_existing_prefix(self) -> None:
        assert add_ticker_prefix("sh.600519") == "sh.600519"
        assert add_ticker_prefix("abc") == "abc"


class TestNextBusinessDays:
    def test_generates_n_business_days(self) -> None:
        days = next_business_days("2026-08-10", 3)  # 8-10 是周一
        assert len(days) == 3
        assert all(isinstance(d, pd.Timestamp) for d in days)
        # 周末应被跳过：8-10(一),8-11(二),8-12(三)
        assert days[0].date().isoformat() == "2026-08-11"

    def test_skips_weekend(self) -> None:
        # 2026-08-14 是周五，之后应跳过周六周日
        days = next_business_days("2026-08-14", 2)
        assert days[0].date().isoformat() == "2026-08-17"  # 下周一


class TestValidateDataFreshness:
    def test_fresh_passes(self) -> None:
        df = pd.DataFrame({"timestamps": ["2026-08-07", "2026-08-10"]})
        validate_data_freshness(df, "2026-08-11", "sh.600519", max_gap_trading_days=10)

    def test_missing_timestamps_raises(self) -> None:
        df = pd.DataFrame({"close": [1.0]})
        try:
            validate_data_freshness(df, "2026-08-11", "sh.600519")
        except RuntimeError as e:
            assert "缺少 timestamps 列" in str(e)
        else:
            raise AssertionError("应抛 RuntimeError")

    def test_future_data_raises(self) -> None:
        df = pd.DataFrame({"timestamps": ["2026-08-12"]})
        try:
            validate_data_freshness(df, "2026-08-11", "sh.600519")
        except RuntimeError as e:
            assert "数据未来化" in str(e)
        else:
            raise AssertionError("应抛 RuntimeError")

    def test_stale_data_raises(self) -> None:
        df = pd.DataFrame({"timestamps": ["2026-07-01"]})
        try:
            validate_data_freshness(df, "2026-08-11", "sh.600519", max_gap_trading_days=10)
        except RuntimeError as e:
            assert "数据过旧" in str(e)
        else:
            raise AssertionError("应抛 RuntimeError")
