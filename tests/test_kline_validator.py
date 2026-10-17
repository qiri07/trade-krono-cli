"""K 线数据校验测试 — 时间升序、去重、缺失K线、脏数据检测。"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from trade_krono_cli.utils.kline_validator import (
    KlineValidationResult,
    detect_dirty_data,
    detect_missing_dates,
    validate_kline_complete,
    validate_kline_dedup,
    validate_kline_order,
)


class TestValidateKlineOrder:
    """测试时间升序校验。"""

    def test_valid_ordered_data(self) -> None:
        """正常升序数据应通过校验。"""
        df = pd.DataFrame(
            {
                "timestamps": [datetime(2024, 1, i) for i in range(1, 11)],
                "close": list(range(10, 0, -1)),
            }
        )
        result = validate_kline_order(df, "sh.600519")
        assert result.is_valid
        assert len(result.errors) == 0

    def test_unordered_data(self) -> None:
        """无序数据应返回错误。"""
        df = pd.DataFrame(
            {
                "timestamps": [
                    datetime(2024, 1, 3),
                    datetime(2024, 1, 1),
                    datetime(2024, 1, 2),
                ],
                "close": [3.0, 1.0, 2.0],
            }
        )
        result = validate_kline_order(df, "sh.600519")
        assert not result.is_valid
        assert any("非升序" in e for e in result.errors)

    def test_duplicate_timestamps(self) -> None:
        """重复时间戳应返回警告。"""
        df = pd.DataFrame(
            {
                "timestamps": [
                    datetime(2024, 1, 1),
                    datetime(2024, 1, 1),
                    datetime(2024, 1, 2),
                ],
                "close": [1.0, 1.0, 2.0],
            }
        )
        result = validate_kline_order(df, "sh.600519")
        assert result.is_valid  # 重复不影响升序
        assert any("重复" in w for w in result.warnings)

    def test_missing_timestamps_column(self) -> None:
        """缺少 timestamps 列应返回错误。"""
        df = pd.DataFrame({"close": [1.0, 2.0, 3.0]})
        result = validate_kline_order(df, "sh.600519")
        assert not result.is_valid
        assert any("缺少 timestamps" in e for e in result.errors)


class TestValidateKlineDedup:
    """测试去重校验。"""

    def test_no_duplicates(self) -> None:
        """无重复数据应通过校验。"""
        df = pd.DataFrame(
            {
                "timestamps": [datetime(2024, 1, i) for i in range(1, 11)],
            }
        )
        result = validate_kline_dedup(df, "sh.600519")
        assert result.is_valid

    def test_with_duplicates(self) -> None:
        """有重复数据应返回警告。"""
        df = pd.DataFrame(
            {
                "timestamps": [
                    datetime(2024, 1, 1),
                    datetime(2024, 1, 1),
                    datetime(2024, 1, 2),
                ],
            }
        )
        result = validate_kline_dedup(df, "sh.600519")
        assert any("重复" in w for w in result.warnings)


class TestDetectMissingDates:
    """测试缺失日期检测。"""

    def test_no_missing_weekdays(self) -> None:
        """连续工作日无缺失。"""
        dates = [datetime(2024, 1, i) for i in range(1, 32) if datetime(2024, 1, i).weekday() < 5]
        df = pd.DataFrame({"timestamps": dates})
        result = detect_missing_dates(df, "sh.600519")
        assert result.is_valid

    def test_missing_dates(self) -> None:
        """有缺失日期应返回警告。"""
        dates = [
            datetime(2024, 1, 1),
            datetime(2024, 1, 2),
            datetime(2024, 1, 8),  # 跳过 3-7
        ]
        df = pd.DataFrame({"timestamps": dates})
        result = detect_missing_dates(df, "sh.600519")
        assert any("缺失" in w for w in result.warnings)


class TestDetectDirtyData:
    """测试脏数据检测。"""

    def test_normal_data(self) -> None:
        """正常数据应通过校验。"""
        df = pd.DataFrame(
            {
                "open": [10.0] * 10,
                "high": [11.0] * 10,
                "low": [9.0] * 10,
                "close": [10.5] * 10,
                "volume": [1000.0] * 10,
            }
        )
        result = detect_dirty_data(df, "sh.600519")
        assert result.is_valid

    def test_price_logic_error(self) -> None:
        """high < low 应返回错误。"""
        df = pd.DataFrame(
            {
                "open": [10.0],
                "high": [9.0],  # 错误：high < low
                "low": [11.0],
                "close": [10.5],
            }
        )
        result = detect_dirty_data(df, "sh.600519")
        assert not result.is_valid
        assert any("价格逻辑错误" in e for e in result.errors)

    def test_zero_prices(self) -> None:
        """零值价格应返回警告。"""
        df = pd.DataFrame(
            {
                "open": [0.0, 10.0],
                "high": [0.0, 11.0],
                "low": [0.0, 9.0],
                "close": [0.0, 10.5],
            }
        )
        result = detect_dirty_data(df, "sh.600519")
        assert any("零值" in w for w in result.warnings)


class TestValidateKlineComplete:
    """测试综合校验。"""

    def test_complete_validation(self) -> None:
        """综合校验应包含所有子项结果。"""
        df = pd.DataFrame(
            {
                "timestamps": [datetime(2024, 1, i) for i in range(1, 11)],
                "open": [10.0] * 10,
                "high": [11.0] * 10,
                "low": [9.0] * 10,
                "close": [10.5] * 10,
                "volume": [1000.0] * 10,
            }
        )
        result = validate_kline_complete(df, "sh.600519")
        assert isinstance(result, KlineValidationResult)
        assert result.is_valid

    def test_summary_output(self) -> None:
        """summary() 方法应返回格式正确的字符串。"""
        df = pd.DataFrame(
            {
                "timestamps": [datetime(2024, 1, i) for i in range(1, 11)],
                "close": list(range(10, 0, -1)),
            }
        )
        result = validate_kline_complete(df, "sh.600519")
        summary = result.summary()
        assert "K线校验结果" in summary
        assert "✅" in summary or "❌" in summary
