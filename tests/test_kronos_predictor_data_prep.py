"""trade_krono_cli.kronos_predictor.data_prep 单元测试。

覆盖 DataPreparator.prepare / pad_df_to_length / split_batches / prepare_from_df。
依赖 trade_krono_cli.data.fetch_lookback / next_business_days 通过 unittest.mock 模拟，
不触发真实网络请求。
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from trade_krono_cli.kronos_predictor.data_prep import DataPreparator


def _make_settings(**overrides) -> MagicMock:
    defaults = {
        "kronos_lookback": 400,
        "kronos_pred_len": 30,
    }
    defaults.update(overrides)
    s = MagicMock()
    for k, v in defaults.items():
        setattr(s, k, v)
    return s


def _make_kline_df(n: int = 410) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": [100.0 + i for i in range(n)],
            "high": [101.0 + i for i in range(n)],
            "low": [99.0 + i for i in range(n)],
            "close": [100.0 + i for i in range(n)],
            "volume": [1e6] * n,
            "amount": [1e8] * n,
            "timestamps": [datetime(2026, 8, 1)] * n,
        }
    )


class TestDataPreparatorPrepare:
    """DataPreparator.prepare 测试（mock fetch_lookback）。"""

    def test_prepare_success(self) -> None:
        settings = _make_settings(kronos_lookback=5, kronos_pred_len=3)
        df = _make_kline_df(10)
        with patch(
            "trade_krono_cli.kronos_predictor.data_prep.fetch_lookback", return_value=df
        ), patch(
            "trade_krono_cli.kronos_predictor.data_prep.next_business_days",
            return_value=[datetime(2026, 8, 12), datetime(2026, 8, 13), datetime(2026, 8, 14)],
        ):
            prep = DataPreparator(settings)
            x_df, x_ts, y_ts, last_close = prep.prepare("sh.600519", "2026-08-11")

        assert isinstance(x_df, pd.DataFrame)
        assert len(x_df) == 5
        assert list(x_df.columns) == ["open", "high", "low", "close", "volume", "amount"]
        assert isinstance(x_ts, pd.Series)
        assert len(y_ts) == 3
        assert last_close == 100.0 + 9  # df close last = 109.0

    def test_prepare_insufficient_data_raises(self) -> None:
        settings = _make_settings(kronos_lookback=400, kronos_pred_len=30)
        df = _make_kline_df(10)
        with patch(
            "trade_krono_cli.kronos_predictor.data_prep.fetch_lookback", return_value=df
        ):
            prep = DataPreparator(settings)
            with pytest.raises(RuntimeError, match="数据不足"):
                prep.prepare("sh.600519", "2026-08-11")


class TestDataPreparatorStatic:
    """pad_df_to_length / split_batches 静态方法测试。"""

    def test_pad_df_to_length_no_pad_when_enough(self) -> None:
        df = _make_kline_df(10)
        result = DataPreparator.pad_df_to_length(df, 5)
        assert len(result) == 5
        pd.testing.assert_frame_equal(result, df.tail(5))

    def test_pad_df_to_length_pads_when_short(self) -> None:
        df = _make_kline_df(3)
        result = DataPreparator.pad_df_to_length(df, 5)
        assert len(result) == 5
        # 前面补最后一行，尾部为原 df
        assert result.iloc[-1].equals(df.iloc[-1])

    def test_split_batches(self) -> None:
        items = [(i, f"t{i}") for i in range(10)]
        batches = DataPreparator.split_batches(items, 3)
        assert batches == [
            [(0, "t0"), (1, "t1"), (2, "t2")],
            [(3, "t3"), (4, "t4"), (5, "t5")],
            [(6, "t6"), (7, "t7"), (8, "t8")],
            [(9, "t9")],
        ]

    def test_split_batches_empty(self) -> None:
        assert DataPreparator.split_batches([], 3) == []


class TestDataPreparatorPrepareFromDf:
    """prepare_from_df 测试。"""

    def test_prepare_from_df(self) -> None:
        df = _make_kline_df(500)
        settings = _make_settings(kronos_lookback=5, kronos_pred_len=3)
        with patch(
            "trade_krono_cli.kronos_predictor.data_prep.next_business_days",
            return_value=[datetime(2026, 8, 12), datetime(2026, 8, 13), datetime(2026, 8, 14)],
        ):
            x_df, x_ts, y_ts, last_close = DataPreparator.prepare_from_df(
                df, "sh.600519", "2026-08-11", settings
            )

        assert len(x_df) == 5
        assert len(y_ts) == 3
        assert last_close == 100.0 + 499
