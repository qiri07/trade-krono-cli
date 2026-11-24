"""港股 K 线同步：增量窗口与合并逻辑测试。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pandas as pd

import scripts.full_hk_history_sync as sync

if TYPE_CHECKING:
    import pytest


def make_df(dates: list[str], closes: list[float]) -> pd.DataFrame:
    """构造 K 线 DataFrame。"""
    return pd.DataFrame(
        {
            "timestamps": pd.to_datetime(dates),
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": [1e6] * len(closes),
            "amount": [1e8] * len(closes),
        }
    )


class FakeResult:
    """模拟 KlineData。"""

    def __init__(self, df: pd.DataFrame) -> None:
        self._df = df
        self.is_empty = len(df) == 0

    def to_dataframe(self) -> pd.DataFrame:
        return self._df


class FakeFactory:
    """记录调用参数的假工厂。"""

    def __init__(self, result: FakeResult) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    def fetch_kline_from_provider(
        self, provider_name: str, ticker: str, start_date: str, end_date: str, **kwargs: Any
    ) -> FakeResult:
        self.calls.append(
            {
                "provider": provider_name,
                "ticker": ticker,
                "start": start_date,
                "end": end_date,
            }
        )
        return self.result


class TestFetchAndStoreIncremental:
    def test_fetch_window_starts_after_cached_end(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """增量模式从「缓存最后交易日 + 1 天」开始，不重复拉取历史。"""
        old = make_df(["2026-11-20", "2026-11-21"], [10.0, 11.0])
        new = make_df(["2026-11-22", "2026-11-23"], [12.0, 13.0])
        monkeypatch.setattr(sync, "_load_cached_df", lambda ticker: old)
        written: list[pd.DataFrame] = []
        monkeypatch.setattr(sync, "_write_df", lambda ticker, df: written.append(df))

        factory = FakeFactory(FakeResult(new))
        ticker, ok, detail = sync.fetch_and_store_hk(
            factory, "hk.00700", "2020-01-01", "2026-11-23", cached_end="2026-11-21"
        )

        assert ok is True
        assert ticker == "hk.00700"
        assert factory.calls[0]["provider"] == "akshare_hk"
        assert factory.calls[0]["start"] == "2026-11-22"
        assert len(written) == 1
        assert list(written[0]["close"]) == [10.0, 11.0, 12.0, 13.0]
        assert "rows=4" in detail

    def test_overlapping_rows_are_deduplicated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Provider 返回重叠区间时按 timestamps 去重，保留新值。"""
        old = make_df(["2026-11-20", "2026-11-21"], [10.0, 11.0])
        new = make_df(["2026-11-21", "2026-11-22"], [99.0, 12.0])
        monkeypatch.setattr(sync, "_load_cached_df", lambda ticker: old)
        written: list[pd.DataFrame] = []
        monkeypatch.setattr(sync, "_write_df", lambda ticker, df: written.append(df))

        sync.fetch_and_store_hk(
            FakeFactory(FakeResult(new)),
            "hk.00700",
            "2020-01-01",
            "2026-11-22",
            cached_end="2026-11-20",
        )

        result = written[0]
        assert len(result) == 3
        assert list(result["timestamps"].dt.strftime("%Y-%m-%d")) == [
            "2026-11-20",
            "2026-11-21",
            "2026-11-22",
        ]
        assert result.iloc[1]["close"] == 99.0  # 新数据覆盖旧值

    def test_already_up_to_date_skips_fetch(self) -> None:
        factory = FakeFactory(FakeResult(make_df([], [])))
        _, ok, detail = sync.fetch_and_store_hk(
            factory,
            "hk.00700",
            "2020-01-01",
            "2026-11-23",
            cached_end="2026-11-23",
        )

        assert ok is True
        assert detail == "already_up_to_date"
        assert factory.calls == []

    def test_full_mode_ignores_cached_end(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            sync,
            "_load_cached_df",
            lambda ticker: (_ for _ in ()).throw(AssertionError("full 模式不应读旧缓存")),
        )
        written: list[pd.DataFrame] = []
        monkeypatch.setattr(sync, "_write_df", lambda ticker, df: written.append(df))
        factory = FakeFactory(FakeResult(make_df(["2026-11-23"], [13.0])))

        sync.fetch_and_store_hk(
            factory, "hk.00700", "2020-01-01", "2026-11-23", full=True, cached_end="2026-11-20"
        )

        assert factory.calls[0]["start"] == "2020-01-01"
        assert len(written[0]) == 1


class TestNormalizeDf:
    def test_renames_date_column_and_dedupes(self) -> None:
        df = pd.DataFrame({"date": ["2026-11-22", "2026-11-22"], "close": [1.0, 2.0]})
        result = sync._normalize_df(df, "hk.00700")

        assert result is not None
        assert "timestamps" in result.columns
        assert len(result) == 1
        assert result.iloc[0]["close"] == 2.0
