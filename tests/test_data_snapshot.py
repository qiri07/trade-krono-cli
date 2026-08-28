"""测试 DataSnapshot 模块。"""

from __future__ import annotations

import pandas as pd
import pytest

from trade_krono_cli.data_snapshot import (
    DataSnapshot,
    DataSourceSnapshot,
    filter_kline_to_cut_date,
)

# ═══════════════════════════════════════════════════════
# DataSourceSnapshot
# ═══════════════════════════════════════════════════════


class TestDataSourceSnapshot:
    def test_basic_creation(self):
        s = DataSourceSnapshot(
            source="baostock",
            cut_date="2024-06-30",
            latest_date="2024-06-28",
            record_count=250,
        )
        assert s.source == "baostock"
        assert s.cut_date == "2024-06-30"
        assert s.latest_date == "2024-06-28"
        assert s.record_count == 250
        assert s.data_hash == ""

    def test_is_future_before_latest(self):
        s = DataSourceSnapshot(
            source="baostock",
            cut_date="2024-06-30",
            latest_date="2024-06-28",
            record_count=200,
        )
        assert s.is_future("2024-06-27") is False
        assert s.is_future("2024-06-28") is False

    def test_is_future_after_latest(self):
        s = DataSourceSnapshot(
            source="baostock",
            cut_date="2024-06-30",
            latest_date="2024-06-28",
            record_count=200,
        )
        assert s.is_future("2024-06-29") is True
        assert s.is_future("2024-07-01") is True

    def test_to_dict(self):
        s = DataSourceSnapshot(
            source="akshare",
            cut_date="2024-06-30",
            latest_date="2024-06-28",
            record_count=300,
            data_hash="abc123def456",
        )
        d = s.to_dict()
        assert d["source"] == "akshare"
        assert d["cut_date"] == "2024-06-30"
        assert d["latest_date"] == "2024-06-28"
        assert d["record_count"] == 300
        assert d["data_hash"] == "abc123def456"[:16]

    def test_to_dict_empty_hash(self):
        s = DataSourceSnapshot(
            source="mootdx",
            cut_date="2024-01-01",
            latest_date="2024-01-01",
            record_count=100,
        )
        d = s.to_dict()
        assert d["data_hash"] == ""

    def test_frozen(self):
        import dataclasses

        s = DataSourceSnapshot(
            source="test",
            cut_date="2024-01-01",
            latest_date="2024-01-01",
            record_count=10,
        )
        assert dataclasses.is_dataclass(s)
        assert s.__dataclass_params__.frozen is True
        # 冻结 dataclass 不允许直接赋值
        try:
            s.source = "modified"
            pytest.fail("Expected FrozenInstanceError or AttributeError")
        except (dataclasses.FrozenInstanceError, AttributeError):
            pass


# ═══════════════════════════════════════════════════════
# DataSnapshot
# ═══════════════════════════════════════════════════════


class TestDataSnapshot:
    def test_empty_sources(self):
        snap = DataSnapshot(cut_date="2024-06-30")
        assert snap.sources == ()
        assert snap.effective_cut_date() == "2024-06-30"
        sid = snap.snapshot_id
        assert isinstance(sid, str)
        assert len(sid) == 16

    def test_with_sources(self):
        sources = (
            DataSourceSnapshot(
                source="baostock",
                cut_date="2024-06-30",
                latest_date="2024-06-28",
                record_count=250,
            ),
            DataSourceSnapshot(
                source="akshare",
                cut_date="2024-06-30",
                latest_date="2024-06-27",
                record_count=200,
            ),
        )
        snap = DataSnapshot(cut_date="2024-06-30", sources=sources)
        assert len(snap.sources) == 2
        # effective_cut_date 取最晚的 latest_date
        assert snap.effective_cut_date() == "2024-06-28"

    def test_contains_future_data_no_future(self):
        sources = (
            DataSourceSnapshot(
                source="baostock",
                cut_date="2024-06-30",
                latest_date="2024-06-28",
                record_count=250,
            ),
        )
        snap = DataSnapshot(cut_date="2024-06-30", sources=sources)
        # 所有数据源都有 2024-06-28 的数据
        assert snap.contains_future_data("sh.600519", "2024-06-27") is False
        assert snap.contains_future_data("sh.600519", "2024-06-28") is False

    def test_contains_future_data_with_future(self):
        sources = (
            DataSourceSnapshot(
                source="baostock",
                cut_date="2024-06-30",
                latest_date="2024-06-28",
                record_count=250,
            ),
        )
        snap = DataSnapshot(cut_date="2024-06-30", sources=sources)
        # 2024-06-29 超出了 baostock 的数据边界
        assert snap.contains_future_data("sh.600519", "2024-06-29") is True

    def test_contains_future_data_empty_sources(self):
        snap = DataSnapshot(cut_date="2024-06-30")
        # 空 sources → 不认为包含未来数据
        assert snap.contains_future_data("sh.600519", "2024-07-01") is False

    def test_to_dict_roundtrip(self):
        sources = (
            DataSourceSnapshot(
                source="baostock",
                cut_date="2024-06-30",
                latest_date="2024-06-28",
                record_count=250,
            ),
        )
        snap = DataSnapshot(cut_date="2024-06-30", sources=sources, description="test")
        d = snap.to_dict()
        assert d["cut_date"] == "2024-06-30"
        assert d["effective_cut_date"] == "2024-06-28"
        assert len(d["sources"]) == 1
        assert d["description"] == "test"

        restored = DataSnapshot.from_dict(d)
        assert restored.cut_date == snap.cut_date
        assert restored.effective_cut_date() == snap.effective_cut_date()
        assert len(restored.sources) == 1
        assert restored.sources[0].source == "baostock"

    def test_snapshot_id_consistent(self):
        snap1 = DataSnapshot(cut_date="2024-06-30")
        snap2 = DataSnapshot(cut_date="2024-06-30")
        assert snap1.snapshot_id == snap2.snapshot_id

    def test_snapshot_id_different_cut_date(self):
        snap1 = DataSnapshot(cut_date="2024-06-30")
        snap2 = DataSnapshot(cut_date="2024-07-01")
        assert snap1.snapshot_id != snap2.snapshot_id

    def test_frozen(self):
        import dataclasses

        snap = DataSnapshot(cut_date="2024-06-30")
        assert dataclasses.is_dataclass(snap)
        assert snap.__dataclass_params__.frozen is True
        # 冻结 dataclass 不允许直接赋值
        try:
            snap.cut_date = "2024-07-01"
            pytest.fail("Expected FrozenInstanceError or AttributeError")
        except (dataclasses.FrozenInstanceError, AttributeError):
            pass

    def test_description(self):
        snap = DataSnapshot(cut_date="2024-06-30", description="daily run")
        assert snap.description == "daily run"
        d = snap.to_dict()
        assert d["description"] == "daily run"


# ═══════════════════════════════════════════════════════
# filter_kline_to_cut_date
# ═══════════════════════════════════════════════════════


class TestFilterKlineToCutDate:
    def test_none_input(self):
        result = filter_kline_to_cut_date(None, "2024-06-30")
        assert result is None

    def test_empty_df(self):
        df = pd.DataFrame({"timestamps": [], "close": []})
        result = filter_kline_to_cut_date(df, "2024-06-30")
        assert len(result) == 0

    def test_filter_cuts_before_date(self):
        df = pd.DataFrame(
            {
                "timestamps": ["2024-06-20", "2024-06-25", "2024-06-28", "2024-06-30"],
                "close": [100.0, 101.0, 102.0, 103.0],
            }
        )
        result = filter_kline_to_cut_date(df, "2024-06-29")
        # 应包含 2024-06-29 及之前的数据（含 2024-06-28，不含 2024-06-30）
        assert len(result) == 3
        assert str(result.iloc[-1]["timestamps"]) == "2024-06-28"

    def test_filter_exact_cutoff(self):
        df = pd.DataFrame(
            {
                "timestamps": ["2024-06-28", "2024-06-29", "2024-06-30"],
                "close": [100.0, 101.0, 102.0],
            }
        )
        result = filter_kline_to_cut_date(df, "2024-06-29")
        # cutoff=2024-06-29，dates <= cutoff → 包含 29
        assert len(result) == 2
        assert str(result.iloc[-1]["timestamps"]) == "2024-06-29"

    def test_filter_no_data_before_cutoff(self):
        df = pd.DataFrame(
            {
                "timestamps": ["2024-07-01", "2024-07-02"],
                "close": [100.0, 101.0],
            }
        )
        result = filter_kline_to_cut_date(df, "2024-06-30")
        assert len(result) == 0

    def test_custom_date_col(self):
        df = pd.DataFrame(
            {
                "date": ["2024-06-20", "2024-06-25", "2024-06-28"],
                "close": [100.0, 101.0, 102.0],
            }
        )
        result = filter_kline_to_cut_date(df, "2024-06-27", date_col="date")
        assert len(result) == 2

    def test_bad_date_col_returns_original(self):
        df = pd.DataFrame(
            {
                "wrong_col": ["2024-06-20", "2024-06-25"],
                "close": [100.0, 101.0],
            }
        )
        # 日期列不存在时，应 catch 异常并返回原 df
        result = filter_kline_to_cut_date(df, "2024-06-30")
        assert len(result) == 2

    def test_returns_copy(self):
        df = pd.DataFrame(
            {
                "timestamps": ["2024-06-20", "2024-06-25"],
                "close": [100.0, 101.0],
            }
        )
        result = filter_kline_to_cut_date(df, "2024-06-30")
        # 修改 result 不应影响原 df
        result.loc[0, "close"] = 999.0
        assert df.loc[0, "close"] == 100.0
