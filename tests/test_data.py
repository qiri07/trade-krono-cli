"""测试数据层和缓存。"""
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


def test_cache_creation():
    from trade_krono_cli.cache import Cache, get_cache
    cache = Cache()
    assert cache._db_path.exists()
    stats = cache.stats()
    assert isinstance(stats, dict)
    assert "cache_kline_cache" in stats
    assert "cache_ta_cache" in stats
    assert "cache_kronos_cache" in stats


def test_cache_get_set_kline():
    from trade_krono_cli.cache import Cache
    import pandas as pd
    cache = Cache()
    cache.clear_all()  # 确保干净状态，避免残留数据干扰
    ticker = "sh.600519"
    start, end = "2025-01-01", "2025-01-31"
    freq = "d"

    # 空缓存
    result = cache.get_kline(ticker, start, end, freq)
    assert result is None

    # 写入
    df = pd.DataFrame({
        "timestamps": pd.date_range("2025-01-01", periods=10, freq="D"),
        "open": [100.0] * 10,
        "high": [101.0] * 10,
        "low": [99.0] * 10,
        "close": [100.5] * 10,
        "volume": [1000.0] * 10,
        "amount": [100000.0] * 10,
    })
    cache.set_kline(ticker, start, end, freq, df, ttl=3600)

    # 读取
    result = cache.get_kline(ticker, start, end, freq)
    assert result is not None
    assert len(result) == 10
    assert list(result.columns) == ["timestamps", "open", "high", "low", "close", "volume", "amount"]


def test_cache_get_set_ta():
    from trade_krono_cli.cache import Cache
    cache = Cache()
    ticker, date = "sh.600519", "2026-08-11"

    result = cache.get_ta(ticker, date)
    assert result is None

    data = {"signal": "BUY", "confidence": 78.5}
    cache.set_ta(ticker, date, data, ttl=3600)

    result = cache.get_ta(ticker, date)
    assert result == data


def test_cache_get_set_kronos():
    from trade_krono_cli.cache import Cache
    cache = Cache()
    ticker, date = "sh.600519", "2026-08-11"
    pred_len = 30

    result = cache.get_kronos(ticker, date, pred_len)
    assert result is None

    data = {"direction": "UP", "expected_change_pct": 3.2}
    cache.set_kronos(ticker, date, pred_len, data, ttl=3600)

    result = cache.get_kronos(ticker, date, pred_len)
    assert result == data


def test_cache_clear_all():
    from trade_krono_cli.cache import Cache
    cache = Cache()
    count = cache.clear_all()
    assert count >= 0
    stats = cache.stats()
    assert all(v == 0 for v in stats.values())


def test_cache_ttl_expiration():
    from trade_krono_cli.cache import Cache
    import time
    cache = Cache()
    ticker, date = "sh.600519", "2026-08-11"

    data = {"test": True}
    cache.set_ta(ticker, date, data, ttl=0)  # 立即过期
    time.sleep(0.1)

    result = cache.get_ta(ticker, date)
    assert result is None


# ── validate_data_freshness ──────────────────────────────────────────────────

class TestValidateDataFreshness:
    """数据新鲜度校验测试。"""

    def _make_df(self, dates):
        """创建最小化 K 线 DataFrame。"""
        import pandas as pd
        return pd.DataFrame({
            "timestamps": pd.to_datetime(dates),
            "open": [100.0] * len(dates),
            "high": [101.0] * len(dates),
            "low": [99.0] * len(dates),
            "close": [100.0] * len(dates),
            "volume": [1e6] * len(dates),
            "amount": [1e8] * len(dates),
        })

    def test_fresh_data_passes(self):
        """数据最后一天是评估日期当天 → 通过。"""
        from trade_krono_cli.data import validate_data_freshness
        df = self._make_df(["2026-08-10", "2026-08-11"])
        # 不应抛异常
        validate_data_freshness(df, "2026-08-11", "sh.600519")

    def test_one_day_gap_passes(self):
        """数据最后一天是评估日期前一天 → 通过。"""
        from trade_krono_cli.data import validate_data_freshness
        df = self._make_df(["2026-08-08", "2026-08-11"])  # 周五→周一，间隔1天
        validate_data_freshness(df, "2026-08-11", "sh.600519")

    def test_suspension_raises(self):
        """数据最后一天距评估日超过 10 个交易日 → 抛异常（疑似停牌）。"""
        from trade_krono_cli.data import validate_data_freshness
        df = self._make_df(["2026-06-01", "2026-06-02"])  # 早于评估日约 2 个月
        with pytest.raises(RuntimeError, match="数据过旧|疑似停牌"):
            validate_data_freshness(df, "2026-08-11", "sh.600519")

    def test_future_data_raises(self):
        """数据最后一天晚于评估日期 → 抛异常（数据未来化）。"""
        from trade_krono_cli.data import validate_data_freshness
        df = self._make_df(["2026-08-11", "2026-08-12"])  # 含未来日期
        with pytest.raises(RuntimeError, match="数据未来化"):
            validate_data_freshness(df, "2026-08-11", "sh.600519")

    def test_missing_timestamps_column_raises(self):
        """缺少 timestamps 列 → 抛异常。"""
        import pandas as pd
        from trade_krono_cli.data import validate_data_freshness
        df = pd.DataFrame({"close": [100.0]})
        with pytest.raises(RuntimeError, match="timestamps"):
            validate_data_freshness(df, "2026-08-11", "sh.600519")

    def test_custom_max_gap(self):
        """自定义 max_gap_trading_days 时阈值更严格。"""
        from trade_krono_cli.data import validate_data_freshness
        df = self._make_df(["2026-07-01", "2026-07-02"])  # 距评估日约 40 天
        # 默认 max_gap=10 会报错
        with pytest.raises(RuntimeError):
            validate_data_freshness(df, "2026-08-11", "sh.600519", max_gap_trading_days=5)
