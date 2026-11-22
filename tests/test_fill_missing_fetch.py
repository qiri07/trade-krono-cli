"""tests for scripts.fill_missing.fetch — 历史回补的写入合并与日期口径。

重点覆盖：
- :func:`_merge_with_cached`：库内历史与新拉取数据的并集合并（同日以新数据为准）。
- :func:`fetch_full_range`：数据源滞后时不得让库内 end 倒退（历史回退回归）。
- :func:`fetch_full_range`：终点取「应已落库的最近交易日」，不落盘中未完成 bar。
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pandas as pd
import pytest

import scripts.fill_missing.fetch as fetcher
from trade_krono_cli.data_providers.base import KlineData


def _kline(dates: list[str], close: list[float]) -> KlineData:
    """按给定日期与收盘价构造 KlineData。"""
    return KlineData(
        timestamps=[datetime.strptime(d, "%Y-%m-%d") for d in dates],
        open=list(close),
        high=list(close),
        low=list(close),
        close=list(close),
        volume=[100.0] * len(close),
        amount=[100.0] * len(close),
    )


def _df(dates: list[str], close: list[float]) -> pd.DataFrame:
    """按给定日期与收盘价构造 DataFrame（与 KlineData.to_dataframe 同形）。"""
    return pd.DataFrame(
        {
            "timestamps": pd.to_datetime(dates),
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": [100.0] * len(close),
            "amount": [100.0] * len(close),
        }
    )


class _FakeCache:
    """最小缓存替身：只实现 get_kline / set_kline。"""

    def __init__(self, existing: pd.DataFrame | None = None) -> None:
        self.existing = existing
        self.written: list[tuple[str, str, str, pd.DataFrame]] = []

    def get_kline(
        self,
        ticker: str,
        start: str,
        end: str,
        freq: str,
        adjustflag: str = "1",
    ) -> pd.DataFrame | None:
        return None if self.existing is None else self.existing.copy()

    def set_kline(
        self,
        ticker: str,
        start: str,
        end: str,
        freq: str,
        df: pd.DataFrame,
        ttl: float = 86400,
        adjustflag: str = "1",
    ) -> None:
        self.written.append((ticker, start, end, df))


class TestMergeWithCached:
    """_merge_with_cached：并集合并、同日以新数据为准、结果升序。"""

    def test_returns_fresh_data_when_cache_empty(self) -> None:
        """库内无记录时直接返回新数据（已排序）。"""
        cache = _FakeCache(existing=None)
        merged = fetcher._merge_with_cached(
            cache, "sh.600519", _df(["2026-11-19", "2026-11-18"], [2.0, 1.0])
        )
        assert list(merged["timestamps"].dt.strftime("%Y-%m-%d")) == [
            "2026-11-18",
            "2026-11-19",
        ]

    def test_union_keeps_cached_dates_missing_from_fresh(self) -> None:
        """新数据缺少的交易日仍保留在库内结果里（不丢历史）。"""
        cached = _df(["2026-11-17", "2026-11-19"], [1.0, 3.0])
        merged = fetcher._merge_with_cached(
            _FakeCache(cached), "hk.09677", _df(["2026-11-17", "2026-09-30"], [9.0, 8.0])
        )
        assert list(merged["timestamps"].dt.strftime("%Y-%m-%d")) == [
            "2026-09-30",
            "2026-11-17",
            "2026-11-19",
        ]

    def test_same_day_prefers_fresh_value(self) -> None:
        """同一交易日出现两次时，以新拉取的数据为准。"""
        cached = _df(["2026-11-19"], [1.0])
        merged = fetcher._merge_with_cached(
            _FakeCache(cached), "sh.600519", _df(["2026-11-19"], [2.0])
        )
        assert len(merged) == 1
        assert float(merged["close"].iloc[0]) == pytest.approx(2.0)


class TestFetchFullRangeMerge:
    """fetch_full_range：写入为并集合并，且终点取期望交易日。"""

    def test_lagging_source_does_not_roll_back_end_date(self, monkeypatch) -> None:
        """数据源最新日早于库内 end 时，库内 end 不得倒退（历史回退回归）。"""
        cached = _df(["2026-11-18", "2026-11-19"], [1.0, 2.0])
        cache = _FakeCache(cached)

        class _LaggingProvider:
            def fetch_kline(
                self,
                ticker: str,
                start_date: str,
                end_date: str,
                frequency: str = "d",
                adjustflag: str = "1",
            ) -> KlineData | None:
                return _kline(["2026-09-29", "2026-09-30"], [3.0, 4.0])

        monkeypatch.setattr(fetcher, "get_cache", lambda: cache)
        monkeypatch.setattr(fetcher, "get_expected_date", lambda: "2026-11-19")

        factory = SimpleNamespace(get_provider=lambda name: _LaggingProvider())
        provider, n_rows, detail = fetcher.fetch_full_range(factory, "hk.09677", ["lag"])

        assert provider == "lag"
        assert cache.written, "必须写入缓存"
        _, start, end, df = cache.written[0]
        assert start == "2026-09-29"
        assert end == "2026-11-19", "库内较新的 11-19 不能被滞后数据源覆盖掉"
        assert n_rows == len(df) == 4
        assert detail == "2026-09-29~2026-11-19"

    def test_uses_expected_trading_day_as_end(self, monkeypatch) -> None:
        """拉取终点应为期望交易日（收盘后当日 / 盘中上一交易日），而非原始今日。"""
        captured: dict[str, str] = {}
        cache = _FakeCache(None)

        class _RecordingProvider:
            def fetch_kline(
                self,
                ticker: str,
                start_date: str,
                end_date: str,
                frequency: str = "d",
                adjustflag: str = "1",
            ) -> KlineData | None:
                captured["start"] = start_date
                captured["end"] = end_date
                return _kline(["2026-11-19"], [1.0])

        monkeypatch.setattr(fetcher, "get_cache", lambda: cache)
        monkeypatch.setattr(fetcher, "get_expected_date", lambda: "2026-11-19")

        factory = SimpleNamespace(get_provider=lambda name: _RecordingProvider())
        fetcher.fetch_full_range(factory, "sh.600519", ["rec"])

        assert captured["start"] == fetcher.START_DATE
        assert captured["end"] == "2026-11-19"
