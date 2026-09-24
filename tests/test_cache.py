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
        },
    )


# ── _validate_table_name ──────────────────────────────────────────────────────


def test_validate_table_name_allowed() -> None:
    assert _validate_table_name("kline_cache", frozenset({"kline_cache"})) == "kline_cache"
    assert _validate_table_name("ta_cache", frozenset({"kline_cache", "ta_cache"})) == "ta_cache"
    assert _validate_table_name("kronos_cache", frozenset({"kronos_cache"})) == "kronos_cache"


def test_validate_table_name_denied() -> None:
    with pytest.raises(ValueError, match="Unauthorized table"):
        _validate_table_name("forbidden", frozenset({"kline_cache"}))


# ── K 线缓存 ──────────────────────────────────────────────────────────────────


def test_kline_cache_set_and_get(tmp_path) -> None:
    db_path = tmp_path / "cache.db"
    c = Cache(db_path=db_path)
    df = _make_kline_df(5)
    c.set_kline("sh.600519", "2026-01-01", "2026-01-05", "d", df, ttl=3600)
    result = c.get_kline("sh.600519", "2026-01-01", "2026-01-05", "d")
    assert result is not None
    assert len(result) == 5
    assert list(result.columns) == ["timestamps", "open", "close", "high", "low", "volume"]


def test_kline_cache_miss(tmp_path) -> None:
    c = Cache(db_path=tmp_path / "cache.db")
    result = c.get_kline("sh.600519", "2026-01-01", "2026-01-05", "d")
    assert result is None


def test_kline_cache_overwrite(tmp_path) -> None:
    c = Cache(db_path=tmp_path / "cache.db")
    # 创建相邻区间的 DataFrame（共享端点）
    df1 = _make_kline_df(3)  # 2026-01-01 ~ 2026-01-03
    # 构造第二段：从2026-01-03开始，5天到2026-01-07（确保 timestamps 类型一致）
    dates2 = pd.date_range("2026-01-03", periods=5, freq="D")
    df2 = pd.DataFrame(
        {
            "timestamps": dates2,
            "open": [10.0 + i * 0.1 for i in range(5)],
            "close": [10.5 + i * 0.1 for i in range(5)],
            "high": [11.0 + i * 0.1 for i in range(5)],
            "low": [9.5 + i * 0.1 for i in range(5)],
            "volume": [1_000_000] * 5,
        }
    )
    c.set_kline("sh.600519", "2026-01-01", "2026-01-03", "d", df1, ttl=3600)
    c.set_kline("sh.600519", "2026-01-03", "2026-01-07", "d", df2, ttl=3600)
    result = c.get_kline("sh.600519", "2026-01-01", "2026-01-07", "d")
    assert result is not None
    # 合并相邻区间（3+5行，共享1天），去重后共 3+5-1 = 7 行
    assert len(result) == 7
    # 范围覆盖整个区间
    assert str(result["timestamps"].min())[:10] == "2026-01-01"
    assert str(result["timestamps"].max())[:10] == "2026-01-07"


def test_kline_cache_ttl_expiry(tmp_path) -> None:
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


def test_kline_cache_ttl_expire(tmp_path) -> None:
    """TTL 为正值且已过期时返回 None。"""
    c = Cache(db_path=tmp_path / "cache.db")
    df = _make_kline_df(3)
    c.set_kline("sh.600519", "2026-01-01", "2026-01-05", "d", df, ttl=0.001)
    time.sleep(0.01)  # 等待过期
    result = c.get_kline("sh.600519", "2026-01-01", "2026-01-05", "d")
    assert result is None


def test_kline_cache_warm_history_partial(tmp_path, monkeypatch) -> None:
    """预热函数：fetch_kline 返回空 DataFrame 时返回 (0, 0)。"""
    c = Cache(db_path=tmp_path / "cache.db")
    monkeypatch.setattr("trade_krono_cli.data.fetch_kline", lambda *a, **kw: None)
    fetched, cached = c.warm_history("sh.600519", "2026-08-11", lookback_days=30)
    assert fetched == 0
    assert cached == 0


# ── TA 缓存 ───────────────────────────────────────────────────────────────────


def test_ta_cache_set_and_get(tmp_path) -> None:
    c = Cache(db_path=tmp_path / "cache.db")
    data = {"signal": "BUY", "confidence": 85.0, "thesis": "基本面良好"}
    c.set_ta("sh.600519", "2026-08-11", data, config_hash="abc123")
    result = c.get_ta("sh.600519", "2026-08-11", config_hash="abc123")
    assert result == data


def test_ta_cache_miss(tmp_path) -> None:
    c = Cache(db_path=tmp_path / "cache.db")
    result = c.get_ta("sh.600519", "2026-08-11", config_hash="abc123")
    assert result is None


def test_ta_cache_config_hash_mismatch(tmp_path) -> None:
    c = Cache(db_path=tmp_path / "cache.db")
    data = {"signal": "BUY"}
    c.set_ta("sh.600519", "2026-08-11", data, config_hash="hash_a")
    result = c.get_ta("sh.600519", "2026-08-11", config_hash="hash_b")
    assert result is None


def test_ta_cache_ttl_expire(tmp_path) -> None:
    c = Cache(db_path=tmp_path / "cache.db")
    c.set_ta("sh.600519", "2026-08-11", {"signal": "BUY"}, ttl=0.001)
    time.sleep(0.01)
    result = c.get_ta("sh.600519", "2026-08-11")
    assert result is None


# ── Kronos 缓存 ────────────────────────────────────────────────────────────────


def test_kronos_cache_set_and_get(tmp_path) -> None:
    c = Cache(db_path=tmp_path / "cache.db")
    data = {"direction": "UP", "expected_change_pct": 3.2}
    c.set_kronos(
        "sh.600519",
        "2026-08-11",
        pred_len=30,
        result=data,
        sample_count=5,
        config_hash="xyz",
    )
    result = c.get_kronos("sh.600519", "2026-08-11", pred_len=30, sample_count=5, config_hash="xyz")
    assert result == data


def test_kronos_cache_miss(tmp_path) -> None:
    c = Cache(db_path=tmp_path / "cache.db")
    result = c.get_kronos("sh.600519", "2026-08-11", pred_len=30, sample_count=5)
    assert result is None


def test_kronos_cache_params_mismatch(tmp_path) -> None:
    """pred_len 或 sample_cnt 不匹配时视为 miss。"""
    c = Cache(db_path=tmp_path / "cache.db")
    c.set_kronos("sh.600519", "2026-08-11", pred_len=30, result={"direction": "UP"}, sample_count=5)
    result = c.get_kronos("sh.600519", "2026-08-11", pred_len=30, sample_count=1)
    assert result is None


# ── Schema 迁移 ────────────────────────────────────────────────────────────────


def test_schema_migration_adds_columns(tmp_path) -> None:
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


# ── K线缓存：pickle回退与日期范围查询 ──────────────────────────────────────────


def test_kline_pickle_fallback(tmp_path) -> None:
    """pd.read_pickle失败时回退到pickle.loads（line 49-57）。"""
    import pickle as std_pickle

    c = Cache(db_path=tmp_path / "cache.db")
    df = _make_kline_df(3)
    c.set_kline("sh.600519", "2026-01-01", "2026-01-03", "d", df, ttl=3600)

    # 用标准pickle序列化原始数据，模拟旧格式（无hash校验）
    raw_buf = std_pickle.dumps(df)
    conn = c._conn
    conn.execute(
        "UPDATE kline_cache SET data = ?, data_hash = NULL WHERE ticker = ? AND start = ?",
        (raw_buf, "sh.600519", "2026-01-01"),
    )
    conn.commit()
    result = c.get_kline("sh.600519", "2026-01-01", "2026-01-03", "d")
    assert result is not None
    assert len(result) == 3


def test_kline_data_hash_verification_pass(tmp_path) -> None:
    """新格式数据：hash 校验通过，正常返回。"""
    import hashlib

    c = Cache(db_path=tmp_path / "cache.db")
    df = _make_kline_df(3)
    c.set_kline("sh.600519", "2026-01-01", "2026-01-03", "d", df, ttl=3600)

    # 验证 hash 已存储
    row = c._conn.execute(
        "SELECT data, data_hash FROM kline_cache WHERE ticker=? AND start=?",
        ("sh.600519", "2026-01-01"),
    ).fetchone()
    assert row is not None
    data_bytes, stored_hash = row
    assert stored_hash is not None
    expected = hashlib.sha256(b"TKC1" + data_bytes).hexdigest()
    assert stored_hash == expected

    # 读取应成功
    result = c.get_kline("sh.600519", "2026-01-01", "2026-01-03", "d")
    assert result is not None
    assert len(result) == 3


def test_kline_data_hash_verification_rejects_tampered(tmp_path) -> None:
    """新格式数据：hash 校验失败时跳过被篡改的记录，返回 None。"""
    c = Cache(db_path=tmp_path / "cache.db")
    df = _make_kline_df(3)
    c.set_kline("sh.600519", "2026-01-01", "2026-01-03", "d", df, ttl=3600)

    # 篡改缓存数据
    conn = c._conn
    conn.execute("UPDATE kline_cache SET data = X'deadbeef' WHERE ticker=?", ("sh.600519",))
    conn.commit()

    # 读取应返回 None（唯一一条记录被完整性校验拦截）
    result = c.get_kline("sh.600519", "2026-01-01", "2026-01-03", "d")
    assert result is None


def test_get_cached_date_range_with_overlap(tmp_path) -> None:
    """有重叠区间时正确合并日期范围（line 195,199,205）。"""
    c = Cache(db_path=tmp_path / "cache.db")
    df1 = _make_kline_df(5)  # 2026-01-01 ~ 2026-01-05
    df2 = _make_kline_df(5)  # 2026-01-04 ~ 2026-01-08（与df1重叠）
    df2["timestamps"] = pd.date_range("2026-01-04", periods=5, freq="D")
    c.set_kline("sh.600519", "2026-01-01", "2026-01-05", "d", df1, ttl=-1)
    c.set_kline("sh.600519", "2026-01-04", "2026-01-08", "d", df2, ttl=-1)
    result = c.get_cached_date_range("sh.600519", freq="d")
    assert result is not None
    assert result[0] == "2026-01-01"
    assert result[1] == "2026-01-08"


def test_get_cached_date_range_no_data(tmp_path) -> None:
    """无缓存数据时返回None。"""
    c = Cache(db_path=tmp_path / "cache.db")
    result = c.get_cached_date_range("sh.999999", freq="d")
    assert result is None


def test_get_cached_date_range_all_expired(tmp_path) -> None:
    """所有记录TTL过期时返回None。"""
    import time as _time

    c = Cache(db_path=tmp_path / "cache.db")
    df = _make_kline_df(3)
    c.set_kline("sh.600519", "2026-01-01", "2026-01-03", "d", df, ttl=0.001)
    _time.sleep(0.01)
    result = c.get_cached_date_range("sh.600519", freq="d")
    assert result is None
