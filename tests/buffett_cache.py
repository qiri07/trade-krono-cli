"""buffett_cache — 巴菲特筛选 SQLite 缓存层。

封装所有缓存操作（初始化 / 读写 / TTL / 清理），与业务逻辑完全解耦。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

_CST = timezone(timedelta(hours=8))

# ── TTL 配置（秒）───────────────────────────────────────────────────────────────
# 根据数据更新频率分层：估值每日波动但阈值筛选有缓冲，财务/利润按季报/年报周期。
TTL_STOCKS = 30 * 86400  # 股票列表 30天
TTL_VALUATION = 7 * 86400  # 估值快照 7天（PE/PB每日波动但阈值筛选有缓冲带）
TTL_FINANCIALS = 90 * 86400  # 财务指标 90天（季度更新）
TTL_INCOME = 180 * 86400  # 利润表 180天（年报更新）
TTL_CFO = 180 * 86400  # 现金流 180天（年报更新）

# ── 路径 ────────────────────────────────────────────────────────────────────────
CACHE_DIR = Path("outputs/cache")
CACHE_DB = CACHE_DIR / "buffett_cache.db"

# ── 表名常量 ────────────────────────────────────────────────────────────────────
_TABLE_STOCKS = "cache_stocks"
_TABLE_VALUATIONS = "cache_valuations"
_TABLE_FINANCIALS = "cache_financials"
_TABLE_INCOME = "cache_income"
_TABLE_CFO = "cache_cfo"

# 内存缓存 key → DB 表的映射
_CACHE_TABLE_MAP = [
    (_TABLE_VALUATIONS, "valuations"),
    (_TABLE_FINANCIALS, "financials"),
    (_TABLE_INCOME, "income"),
    (_TABLE_CFO, "cfo"),
]

# DB 表 → TTL（秒）的映射
_TABLE_TTL_MAP = {
    _TABLE_VALUATIONS: TTL_VALUATION,
    _TABLE_FINANCIALS: TTL_FINANCIALS,
    _TABLE_INCOME: TTL_INCOME,
    _TABLE_CFO: TTL_CFO,
}


def init_cache() -> sqlite3.Connection:
    """初始化缓存数据库，创建所需表。"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(CACHE_DB.resolve())
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {_TABLE_STOCKS} (
            key TEXT PRIMARY KEY, data TEXT NOT NULL, expires_at REAL NOT NULL
        )
    """)
    for table in (_TABLE_VALUATIONS, _TABLE_FINANCIALS, _TABLE_INCOME, _TABLE_CFO):
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {table} (
                key TEXT PRIMARY KEY, data TEXT NOT NULL, expires_at REAL NOT NULL
            )
        """)
    conn.commit()
    return conn


def cache_get(conn: sqlite3.Connection, table: str, key: str) -> dict | None:
    """从缓存读取，未命中或已过期返回 None。"""
    now_ts = datetime.now(_CST).timestamp()
    row = conn.execute(
        f"SELECT data FROM {table} WHERE key = ? AND expires_at > ?", (key, now_ts)
    ).fetchone()
    if row is None:
        return None
    try:
        return json.loads(row[0])
    except (json.JSONDecodeError, TypeError):
        return None


def cache_set(conn: sqlite3.Connection, table: str, key: str, data: dict, ttl: int) -> None:
    """写入缓存，覆盖已有记录。"""
    expires_at = datetime.now(_CST).timestamp() + ttl
    conn.execute(
        f"INSERT OR REPLACE INTO {table} (key, data, expires_at) VALUES (?, ?, ?)",
        (key, json.dumps(data, ensure_ascii=False), expires_at),
    )
    conn.commit()


def cache_get_list(conn: sqlite3.Connection, key: str) -> list | None:
    """从 cache_stocks 表读取列表缓存（股票列表等）。"""
    now_ts = datetime.now(_CST).timestamp()
    row = conn.execute(
        f"SELECT data FROM {_TABLE_STOCKS} WHERE key = ? AND expires_at > ?", (key, now_ts)
    ).fetchone()
    if row is None:
        return None
    try:
        return json.loads(row[0])
    except (json.JSONDecodeError, TypeError):
        return None


def cache_set_list(conn: sqlite3.Connection, key: str, data: list, ttl: int) -> None:
    """写入列表缓存到 cache_stocks 表。"""
    expires_at = datetime.now(_CST).timestamp() + ttl
    conn.execute(
        f"INSERT OR REPLACE INTO {_TABLE_STOCKS} (key, data, expires_at) VALUES (?, ?, ?)",
        (key, json.dumps(data, ensure_ascii=False), expires_at),
    )
    conn.commit()


def cache_clean(conn: sqlite3.Connection) -> int:
    """清理所有过期缓存，返回清理条数。"""
    now_ts = datetime.now(_CST).timestamp()
    total = 0
    for tbl in (_TABLE_STOCKS, _TABLE_VALUATIONS, _TABLE_FINANCIALS, _TABLE_INCOME, _TABLE_CFO):
        cur = conn.execute(f"DELETE FROM {tbl} WHERE expires_at <= ?", (now_ts,))
        total += cur.rowcount
    conn.commit()
    return total


def load_all_cache(conn: sqlite3.Connection) -> dict:
    """预加载所有缓存数据到内存字典，避免多线程并发写 DB。"""
    cache: dict = {"valuations": {}, "financials": {}, "income": {}, "cfo": {}}
    now_ts = datetime.now(_CST).timestamp()
    for tbl, target in _CACHE_TABLE_MAP:
        for row in conn.execute(f"SELECT key, data FROM {tbl} WHERE expires_at > ?", (now_ts,)):
            try:
                cache[target][row[0]] = json.loads(row[1])
            except (json.JSONDecodeError, TypeError):
                pass
    return cache


def save_cache_to_db(conn: sqlite3.Connection, cache: dict) -> int:
    """将内存缓存写入数据库（主线程调用，避免并发锁）。返回写入条数。"""
    now_ts = datetime.now(_CST).timestamp()
    inserted = 0
    for tbl, target in _CACHE_TABLE_MAP:
        ttl = _TABLE_TTL_MAP[tbl]
        for key, data in cache[target].items():
            expires_at = now_ts + ttl
            conn.execute(
                f"INSERT OR REPLACE INTO {tbl} (key, data, expires_at) VALUES (?, ?, ?)",
                (key, json.dumps(data, ensure_ascii=False), expires_at),
            )
            inserted += 1
    conn.commit()
    return inserted
