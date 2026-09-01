"""测试 KlineData 数据模型。"""

from __future__ import annotations

import pandas as pd

from trade_krono_cli.data_providers.base import KlineData


class TestKlineData:
    def test_empty_kline(self):
        kd = KlineData()
        assert kd.is_empty
        assert kd.length == 0

    def test_non_empty_kline(self, sample_kline_data):
        assert not sample_kline_data.is_empty
        assert sample_kline_data.length == 2

    def test_to_dataframe(self, sample_kline_data):

        df = sample_kline_data.to_dataframe()
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 2
        assert list(df.columns) == [
            "timestamps",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "amount",
        ]

    def test_from_dataframe(self, sample_kline_data):
        df = sample_kline_data.to_dataframe()
        kd2 = KlineData.from_dataframe(df)
        assert kd2.length == 2
        assert kd2.close[0] == 101.0
        assert kd2.close[1] == 103.0

    def test_from_dataframe_roundtrip(self, sample_kline_data):
        df = sample_kline_data.to_dataframe()
        kd2 = KlineData.from_dataframe(df)
        assert kd2.open == sample_kline_data.open
        assert kd2.high == sample_kline_data.high
        assert kd2.low == sample_kline_data.low
        assert kd2.close == sample_kline_data.close
        assert kd2.volume == sample_kline_data.volume
        assert kd2.amount == sample_kline_data.amount


# ═══════════════════════════════════════════════════════
# RealtimeQuote / StockMetadata 模型测试
# ═══════════════════════════════════════════════════════
