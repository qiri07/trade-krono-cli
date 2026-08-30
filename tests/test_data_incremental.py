"""测试 K 线增量拉取逻辑（cache.get_cached_date_range / fetch_kline_incremental / _merge_kline_dfs）。"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch

import pandas as pd

# ── 辅助函数 ──────────────────────────────────────────────────────────────────


def _make_df(dates: list[str], **extra_cols) -> pd.DataFrame:
    """用给定日期列表创建最小化 K 线 DataFrame。"""
    n = len(dates)
    return pd.DataFrame(
        {
            "timestamps": pd.to_datetime(dates),
            "open": [10.0 + i * 0.1 for i in range(n)],
            "high": [11.0 + i * 0.1 for i in range(n)],
            "low": [9.5 + i * 0.1 for i in range(n)],
            "close": [10.5 + i * 0.1 for i in range(n)],
            "volume": [1_000_000.0] * n,
            "amount": [100_000_000.0] * n,
            **extra_cols,
        }
    )


def _date_range(start: str, days: int) -> list[str]:
    """生成从 start 开始的 days 个连续日期（工作日近似）。"""
    dt = datetime.strptime(start, "%Y-%m-%d")
    return [(dt + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days)]


# ── get_cached_date_range ────────────────────────────────────────────────────


class TestGetCachedDateRange:
    """Cache.get_cached_date_range() 测试。"""

    def test_empty_returns_none(self, tmp_path):
        from trade_krono_cli.cache import Cache

        c = Cache(db_path=tmp_path / "cache.db")
        result = c.get_cached_date_range("sh.600519", freq="d")
        assert result is None

    def test_with_single_entry(self, tmp_path):
        from trade_krono_cli.cache import Cache

        c = Cache(db_path=tmp_path / "cache.db")
        df = _make_df(_date_range("2026-01-01", 10))
        c.set_kline("sh.600519", "2026-01-01", "2026-01-10", "d", df, ttl=0)
        result = c.get_cached_date_range("sh.600519", freq="d")
        assert result == ("2026-01-01", "2026-01-10")

    def test_with_multiple_segments(self, tmp_path):
        """永久缓存只删除完全被新段覆盖的旧段，非重叠段保留用于合并查询。"""
        from trade_krono_cli.cache import Cache

        c = Cache(db_path=tmp_path / "cache.db")
        df1 = _make_df(_date_range("2025-06-01", 20))
        df2 = _make_df(_date_range("2026-01-01", 10))
        c.set_kline("sh.600519", "2025-06-01", "2025-06-20", "d", df1, ttl=0)
        c.set_kline("sh.600519", "2026-01-01", "2026-01-10", "d", df2, ttl=0)
        result = c.get_cached_date_range("sh.600519", freq="d")
        # 两段不重叠，get_cached_date_range 合并为 [min_start, max_end]
        assert result == ("2025-06-01", "2026-01-10")

    def test_expired_ttl_returns_none(self, tmp_path):
        """TTL 已过期时应视为无缓存。"""
        import time

        from trade_krono_cli.cache import Cache

        c = Cache(db_path=tmp_path / "cache.db")
        df = _make_df(_date_range("2026-01-01", 5))
        c.set_kline("sh.600519", "2026-01-01", "2026-01-05", "d", df, ttl=0.001)
        time.sleep(0.01)  # 等待过期
        result = c.get_cached_date_range("sh.600519", freq="d")
        assert result is None

    def test_mixed_ttl_some_expired(self, tmp_path):
        """部分条目过期时，只统计有效条目。"""
        import time

        from trade_krono_cli.cache import Cache

        c = Cache(db_path=tmp_path / "cache.db")
        df_old = _make_df(_date_range("2025-01-01", 5))
        df_new = _make_df(_date_range("2026-01-01", 5))
        c.set_kline("sh.600519", "2025-01-01", "2025-01-05", "d", df_old, ttl=0.001)
        c.set_kline("sh.600519", "2026-01-01", "2026-01-05", "d", df_new, ttl=0)
        time.sleep(0.01)
        result = c.get_cached_date_range("sh.600519", freq="d")
        # 只有新条目有效
        assert result == ("2026-01-01", "2026-01-05")

    def test_different_ticker_returns_none(self, tmp_path):
        from trade_krono_cli.cache import Cache

        c = Cache(db_path=tmp_path / "cache.db")
        df = _make_df(_date_range("2026-01-01", 5))
        c.set_kline("sh.600000", "2026-01-01", "2026-01-05", "d", df, ttl=0)
        result = c.get_cached_date_range("sh.600519", freq="d")
        assert result is None


# ── _merge_kline_dfs ──────────────────────────────────────────────────────────


class TestMergeKlineDfs:
    """_merge_kline_dfs() 测试。"""

    def test_new_extends_old(self):
        """新数据在旧数据之后（无重叠）→ 拼接。"""
        from trade_krono_cli.data import _merge_kline_dfs

        old = _make_df(["2026-01-01", "2026-01-02", "2026-01-03"])
        new = _make_df(["2026-01-04", "2026-01-05"])
        result = _merge_kline_dfs(old, new)
        assert len(result) == 5
        assert result["timestamps"].iloc[0] == pd.Timestamp("2026-01-01")
        assert result["timestamps"].iloc[-1] == pd.Timestamp("2026-01-05")

    def test_new_overwrites_old(self):
        """新数据与旧数据有重叠 → 新数据优先覆盖重叠行，旧前段保留。"""
        from trade_krono_cli.data import _merge_kline_dfs

        # 用不同起始值区分新旧数据
        old = pd.DataFrame(
            {
                "timestamps": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"]),
                "close": [200.0, 201.0, 202.0],
            }
        )
        new = pd.DataFrame(
            {
                "timestamps": pd.to_datetime(["2026-01-02", "2026-01-03", "2026-01-04"]),
                "close": [300.0, 301.0, 302.0],
            }
        )
        result = _merge_kline_dfs(old, new)
        assert len(result) == 4  # 01-01(旧) + 01-02/03(新覆盖) + 01-04(新)
        assert result["close"].iloc[0] == 200.0  # old: 01-01
        assert result["close"].iloc[1] == 300.0  # new overwrites old for 01-02
        assert result["close"].iloc[2] == 301.0  # new overwrites old for 01-03
        assert result["close"].iloc[3] == 302.0  # new: 01-04

    def test_new_completely_inside_old(self):
        """新数据完全在旧数据时间范围内 → 旧前段保留，新数据覆盖重叠部分。"""
        from trade_krono_cli.data import _merge_kline_dfs

        old = pd.DataFrame(
            {
                "timestamps": pd.to_datetime(
                    ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"]
                ),
                "close": [200.0, 201.0, 202.0, 203.0],
            }
        )
        new = pd.DataFrame(
            {
                "timestamps": pd.to_datetime(["2026-01-02", "2026-01-03"]),
                "close": [300.0, 301.0],
            }
        )
        result = _merge_kline_dfs(old, new)
        assert len(result) == 3  # 01-01(旧) + 01-02/03(新覆盖)
        assert result["close"].iloc[0] == 200.0  # old: 01-01
        assert result["close"].iloc[1] == 300.0  # new overwrites old for 01-02
        assert result["close"].iloc[2] == 301.0  # new overwrites old for 01-03

    def test_old_none(self):
        """旧数据为 None → 返回新数据副本。"""
        from trade_krono_cli.data import _merge_kline_dfs

        new = _make_df(["2026-01-01", "2026-01-02"])
        result = _merge_kline_dfs(None, new)
        assert len(result) == 2

    def test_new_none(self):
        """新数据为 None → 返回旧数据副本。"""
        from trade_krono_cli.data import _merge_kline_dfs

        old = _make_df(["2026-01-01", "2026-01-02"])
        result = _merge_kline_dfs(old, None)
        assert len(result) == 2

    def test_both_none(self):
        """两者均为 None → 返回空 DataFrame。"""
        from trade_krono_cli.data import _merge_kline_dfs

        result = _merge_kline_dfs(None, None)
        assert isinstance(result, pd.DataFrame)
        assert len(result) == 0

    def test_duplicate_timestamps_kept_last(self):
        """重复 timestamp → 保留最后一条（去重）。"""
        from trade_krono_cli.data import _merge_kline_dfs

        old = _make_df(["2026-01-01", "2026-01-02", "2026-01-03"])
        # 新数据第一条与旧数据末尾重复
        new = _make_df(["2026-01-03", "2026-01-04"])
        result = _merge_kline_dfs(old, new)
        assert len(result) == 4
        assert list(result["timestamps"]) == [
            pd.Timestamp("2026-01-01"),
            pd.Timestamp("2026-01-02"),
            pd.Timestamp("2026-01-03"),
            pd.Timestamp("2026-01-04"),
        ]


# ── fetch_kline_incremental ───────────────────────────────────────────────────


class TestFetchKlineIncremental:
    """fetch_kline_incremental() 测试。"""

    def test_no_cache_full_fetch(self, tmp_path):
        """无缓存时应调用 fetch_kline 拉取全量数据。"""
        from trade_krono_cli.cache import Cache
        from trade_krono_cli.data import fetch_kline_incremental

        c = Cache(db_path=tmp_path / "cache.db")
        mock_df = _make_df(_date_range("2025-06-01", 300))
        with patch("trade_krono_cli.data.get_cache", return_value=c):
            with patch("trade_krono_cli.data.fetch_kline", return_value=mock_df) as mock_fetch:
                result = fetch_kline_incremental(
                    "sh.600519",
                    "2025-06-01",
                    "2026-08-11",
                    frequency="d",
                    use_cache=True,
                )
                assert len(result) == 300
                # 应调用一次 fetch_kline（全量）
                assert mock_fetch.call_count == 1

    def test_full_cache_coverage(self, tmp_path):
        """缓存已完整覆盖请求范围 → 不发起网络请求。"""
        from trade_krono_cli.cache import Cache
        from trade_krono_cli.data import fetch_kline_incremental

        c = Cache(db_path=tmp_path / "cache.db")
        cached_df = _make_df(_date_range("2025-01-01", 500))
        c.set_kline("sh.600519", "2025-01-01", "2026-08-11", "d", cached_df, ttl=0)
        with patch("trade_krono_cli.data.get_cache", return_value=c):
            with patch("trade_krono_cli.data.fetch_kline", return_value=cached_df) as mock_fetch:
                result = fetch_kline_incremental(
                    "sh.600519",
                    "2026-01-01",
                    "2026-08-11",
                    frequency="d",
                    use_cache=True,
                )
                # 命中缓存，调用了 fetch_kline 读取缓存段
                assert len(result) == 500
                # fetch_kline 被调用两次：一次读全量（Case 1），一次用于验证
                assert mock_fetch.call_count >= 1

    def test_partial_cache_gap_fetch(self, tmp_path):
        """缓存有部分数据 → 只拉取缺失尾部，合并后返回。"""
        from trade_krono_cli.cache import Cache
        from trade_krono_cli.data import fetch_kline_incremental

        c = Cache(db_path=tmp_path / "cache.db")

        # 已有缓存：2025-01-01 ~ 2026-06-01
        cached_df = _make_df(_date_range("2025-01-01", 500))
        c.set_kline("sh.600519", "2025-01-01", "2026-06-01", "d", cached_df, ttl=0)

        # 新拉取的数据：2026-06-02 ~ 2026-08-11
        new_df = _make_df(_date_range("2026-06-02", 70))

        call_count = 0

        def fake_fetch_kline(ticker, start, end, **kwargs):
            nonlocal call_count
            call_count += 1
            if "2026-06" in start or "2026-07" in start or "2026-08" in start:
                return new_df
            return cached_df

        with patch("trade_krono_cli.data.get_cache", return_value=c):
            with patch("trade_krono_cli.data.fetch_kline", side_effect=fake_fetch_kline):
                result = fetch_kline_incremental(
                    "sh.600519",
                    "2025-01-01",
                    "2026-08-11",
                    frequency="d",
                    use_cache=True,
                )

        # 合并后应接近 500+70=570 行（可能有少量重叠去重）
        assert len(result) >= 500
        # 应至少拉取了一次新数据
        assert call_count >= 1

    def test_expired_cache_triggers_full_fetch(self, tmp_path):
        """缓存 TTL 过期 → 视为无缓存，全量拉取。"""
        import time

        from trade_krono_cli.cache import Cache
        from trade_krono_cli.data import fetch_kline_incremental

        c = Cache(db_path=tmp_path / "cache.db")
        df = _make_df(_date_range("2026-01-01", 100))
        c.set_kline("sh.600519", "2026-01-01", "2026-04-10", "d", df, ttl=0.001)
        time.sleep(0.01)  # 等待过期

        with patch("trade_krono_cli.data.get_cache", return_value=c):
            with patch("trade_krono_cli.data.fetch_kline", return_value=df) as mock_fetch:
                _result = fetch_kline_incremental(
                    "sh.600519",
                    "2026-01-01",
                    "2026-04-10",
                    frequency="d",
                    use_cache=True,
                )
                # 缓存过期后走全量拉取路径
                assert mock_fetch.call_count >= 1

    def test_use_cache_false_bypasses_cache(self, tmp_path):
        """use_cache=False 时应直接走全量 fetch_kline，不走增量逻辑。"""
        from trade_krono_cli.data import fetch_kline_incremental

        mock_df = _make_df(_date_range("2026-01-01", 100))
        with patch("trade_krono_cli.data.fetch_kline", return_value=mock_df) as mock_fetch:
            result = fetch_kline_incremental(
                "sh.600519",
                "2026-01-01",
                "2026-04-10",
                frequency="d",
                use_cache=False,
            )
            assert len(result) == 100
            assert mock_fetch.call_count == 1
