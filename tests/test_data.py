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
