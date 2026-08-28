"""测试缓存层（Cache — TTL 驱动的 SQLite 缓存）。"""

from __future__ import annotations

import time

import pandas as pd
import pytest

from trade_krono_cli.cache import Cache, _validate_table_name

# ── 辅助函数 ──────────────────────────────────────────────────────────────────


def _make_kline_df(n: int = 10) -> pd.DataFrame:
    """生成 N 行模拟 K 线 DataFrame。"""
    dates = pd.date_range("2026-01-01", periods=n, freq="D")
    return pd.DataFrame(
        {
            "timestamps": dates,
            "open": [10.0 + i * 0.1 for i in range(n)],
            "close": [10.5 + i * 0.1 for i in range(n)],
            "high": [11.0 + i * 0.1 for i in range(n)],
            "low": [9.5 + i * 0.1 for i in range(n)],
            "volume": [1_000_000.0] * n,
        }
    )


# ── _validate_table_name ──────────────────────────────────────────────────────


def test_validate_table_name_allowed():
    assert _validate_table_name("kline_cache", frozenset({"kline_cache"})) == "kline_cache"
    assert _validate_table_name("ta_cache", frozenset({"kline_cache", "ta_cache"})) == "ta_cache"
    assert _validate_table_name("kronos_cache", frozenset({"kronos_cache"})) == "kronos_cache"


def test_validate_table_name_denied():
    with pytest.raises(ValueError, match="Unauthorized table"):
        _validate_table_name("forbidden", frozenset({"kline_cache"}))


# ── K 线缓存 ──────────────────────────────────────────────────────────────────


def test_kline_cache_set_and_get(tmp_path):
    db_path = tmp_path / "cache.db"
    c = Cache(db_path=db_path)
    df = _make_kline_df(5)
    c.set_kline("sh.600519", "2026-01-01", "2026-01-05", "d", df, ttl=3600)
    result = c.get_kline("sh.600519", "2026-01-01", "2026-01-05", "d")
    assert result is not None
    assert len(result) == 5
    assert list(result.columns) == ["timestamps", "open", "close", "high", "low", "volume"]


def test_kline_cache_miss(tmp_path):
    c = Cache(db_path=tmp_path / "cache.db")
    result = c.get_kline("sh.600519", "2026-01-01", "2026-01-05", "d")
    assert result is None


def test_kline_cache_overwrite(tmp_path):
    c = Cache(db_path=tmp_path / "cache.db")
    df1 = _make_kline_df(3)
    df2 = _make_kline_df(7)
    c.set_kline("sh.600519", "2026-01-01", "2026-01-05", "d", df1, ttl=3600)
    c.set_kline("sh.600519", "2026-01-01", "2026-01-05", "d", df2, ttl=3600)
    result = c.get_kline("sh.600519", "2026-01-01", "2026-01-05", "d")
    assert result is not None
    assert len(result) == 7


def test_kline_cache_ttl_expiry(tmp_path):
    c = Cache(db_path=tmp_path / "cache.db")
    df = _make_kline_df(5)
    # TTL = 0 → 永久
    c.set_kline("sh.600519", "2026-01-01", "2026-01-05", "d", df, ttl=0.0)
    result = c.get_kline("sh.600519", "2026-01-01", "2026-01-05", "d")
    assert result is not None
    # TTL = -1 → 强制失效
    c.set_kline("sz.000858", "2026-01-01", "2026-01-05", "d", df, ttl=-1.0)
    result = c.get_kline("sz.000858", "2026-01-01", "2026-01-05", "d")
    assert result is None


def test_kline_cache_ttl_expire(tmp_path):
    """TTL 为正值且已过期时返回 None。"""
    c = Cache(db_path=tmp_path / "cache.db")
    df = _make_kline_df(3)
    c.set_kline("sh.600519", "2026-01-01", "2026-01-05", "d", df, ttl=0.001)
    time.sleep(0.01)  # 等待过期
    result = c.get_kline("sh.600519", "2026-01-01", "2026-01-05", "d")
    assert result is None


def test_kline_cache_warm_history_partial(tmp_path, monkeypatch):
    """预热函数：fetch_kline 返回空 DataFrame 时返回 (0, 0)。"""
    c = Cache(db_path=tmp_path / "cache.db")
    monkeypatch.setattr("trade_krono_cli.data.fetch_kline", lambda *a, **kw: None)
    fetched, cached = c.warm_history("sh.600519", "2026-08-11", lookback_days=30)
    assert fetched == 0
    assert cached == 0


# ── TA 缓存 ───────────────────────────────────────────────────────────────────


def test_ta_cache_set_and_get(tmp_path):
    c = Cache(db_path=tmp_path / "cache.db")
    data = {"signal": "BUY", "confidence": 85.0, "thesis": "基本面良好"}
    c.set_ta("sh.600519", "2026-08-11", data, config_hash="abc123")
    result = c.get_ta("sh.600519", "2026-08-11", config_hash="abc123")
    assert result == data


def test_ta_cache_miss(tmp_path):
    c = Cache(db_path=tmp_path / "cache.db")
    result = c.get_ta("sh.600519", "2026-08-11", config_hash="abc123")
    assert result is None


def test_ta_cache_config_hash_mismatch(tmp_path):
    c = Cache(db_path=tmp_path / "cache.db")
    data = {"signal": "BUY"}
    c.set_ta("sh.600519", "2026-08-11", data, config_hash="hash_a")
    result = c.get_ta("sh.600519", "2026-08-11", config_hash="hash_b")
    assert result is None


def test_ta_cache_ttl_expire(tmp_path):
    c = Cache(db_path=tmp_path / "cache.db")
    c.set_ta("sh.600519", "2026-08-11", {"signal": "BUY"}, ttl=0.001)
    time.sleep(0.01)
    result = c.get_ta("sh.600519", "2026-08-11")
    assert result is None


# ── Kronos 缓存 ────────────────────────────────────────────────────────────────


def test_kronos_cache_set_and_get(tmp_path):
    c = Cache(db_path=tmp_path / "cache.db")
    data = {"direction": "UP", "expected_change_pct": 3.2}
    c.set_kronos(
        "sh.600519", "2026-08-11", pred_len=30, result=data, sample_count=5, config_hash="xyz"
    )
    result = c.get_kronos("sh.600519", "2026-08-11", pred_len=30, sample_count=5, config_hash="xyz")
    assert result == data


def test_kronos_cache_miss(tmp_path):
    c = Cache(db_path=tmp_path / "cache.db")
    result = c.get_kronos("sh.600519", "2026-08-11", pred_len=30, sample_count=5)
    assert result is None


def test_kronos_cache_params_mismatch(tmp_path):
    """pred_len 或 sample_cnt 不匹配时视为 miss。"""
    c = Cache(db_path=tmp_path / "cache.db")
    c.set_kronos("sh.600519", "2026-08-11", pred_len=30, result={"direction": "UP"}, sample_count=5)
    result = c.get_kronos("sh.600519", "2026-08-11", pred_len=30, sample_count=1)
    assert result is None


# ── Schema 迁移 ────────────────────────────────────────────────────────────────


def test_schema_migration_adds_columns(tmp_path):
    """新列不存在时，init_db 应自动添加（静默失败）。"""
    db = tmp_path / "cache.db"
    # 直接创建不含 config_hash 的旧表
    conn = __import__("sqlite3").connect(str(db))
    conn.execute("""
        CREATE TABLE ta_cache (
            ticker TEXT NOT NULL, date TEXT NOT NULL,
            ttl REAL NOT NULL, data BLOB NOT NULL, created REAL NOT NULL,
            PRIMARY KEY (ticker, date)
        )
    """)
    conn.commit()
    conn.close()
    # 再次实例化应不抛异常
    c = Cache(db_path=db)
    # 验证列已添加
    info = c._conn.execute("PRAGMA table_info(ta_cache)").fetchall()
    col_names = {row[1] for row in info}
    assert "config_hash" in col_names
    assert "prompt_ver" in col_names
    assert "model_ver" in col_names
