"""港股基本面元数据表读写（hk_metadata）。

统一港股筛选链路中「本地元数据」的 schema 与读写语义，供
``scripts/fetch_hk_metadata.py``（写）与 ``scripts/hk_screen/data_fetcher.py``（读）共用。

关键约定（修复历史遗留的语义冲突）：
- 数值字段缺失一律存 ``None``（SQLite NULL），**禁止**用 0 表示未知；
- 布尔字段采用三态：``True`` / ``False`` / ``None``（未知）；
- 筛选器把 ``None`` 视为「未知、不计失败」，``False`` 才是「明确不满足」；
- 写入为「按键增量 upsert」：只更新记录中出现的列，不会把未提供的列清空，
  因此「只刷新行情」不会冲掉「财务字段」。
"""

from __future__ import annotations

import sqlite3
from typing import Any  # 元数据值类型混杂（float/int/bool/str/None），Any 为合理

from loguru import logger

# 表结构定义：列名 -> SQL 类型（不含主键 ticker、时间戳列）
_COLUMNS: dict[str, str] = {
    "name": "TEXT",
    "industry": "TEXT",
    "pe_ttm": "REAL",
    "pb": "REAL",
    "dividend_yield": "REAL",
    "market_cap": "REAL",
    "turnover_20d_avg": "REAL",
    "turnover_rate_12m_avg": "REAL",
    "listing_date": "TEXT",
    "listing_days": "INTEGER",
    "is_hk_connect": "INTEGER",
    "is_hkd": "INTEGER",
    "is_common_stock": "INTEGER",
    "suspension_days_30d": "INTEGER",
    "has_risk_warning": "INTEGER",
    "debt_to_asset": "REAL",
    "non_gaap_profit_2y": "INTEGER",
    "clean_audit_2y": "INTEGER",
    "operating_cashflow_3y": "INTEGER",
    "revenue_growth_3y": "INTEGER",
    "gross_margin": "REAL",
    "avg_roe_3y": "REAL",
    "top_shareholder_pct": "REAL",
    "max_drawdown_12m": "REAL",
    "rights_issues_12m": "INTEGER",
    "price": "REAL",
    "drawdown_from_high": "REAL",
    "single_day_spike_1y": "REAL",
    "dividend_3y": "INTEGER",
    "payout_ratio": "REAL",
    "annualized_volatility_12m": "REAL",
    "fcf_to_net_profit": "REAL",
}

# 记录级时间戳列（由写入方显式赋值）
_TIMESTAMP_COLUMNS: dict[str, str] = {
    "updated_at": "TEXT",
    "financial_updated_at": "TEXT",
    "profile_updated_at": "TEXT",
}

# 布尔列（读取时 NULL -> None，0/1 -> bool）
_BOOL_COLUMNS: frozenset[str] = frozenset(
    {
        "is_hk_connect",
        "is_hkd",
        "is_common_stock",
        "has_risk_warning",
        "non_gaap_profit_2y",
        "clean_audit_2y",
        "operating_cashflow_3y",
        "revenue_growth_3y",
        "dividend_3y",
    }
)

# 整数列（其余数值列按 float 读取）
_INT_COLUMNS: frozenset[str] = frozenset(
    {"listing_days", "suspension_days_30d", "rights_issues_12m"}
)

_ALL_COLUMNS: tuple[str, ...] = (
    "ticker",
    *_COLUMNS.keys(),
    *_TIMESTAMP_COLUMNS.keys(),
)

# 财务字段集合（判定财务字段是否齐全时使用）
FINANCIAL_COLUMNS: tuple[str, ...] = (
    "debt_to_asset",
    "non_gaap_profit_2y",
    "operating_cashflow_3y",
    "revenue_growth_3y",
    "gross_margin",
    "avg_roe_3y",
)

# 档案字段集合（上市信息、标的类型、港股通）
PROFILE_COLUMNS: tuple[str, ...] = (
    "listing_date",
    "listing_days",
    "is_hk_connect",
    "is_hkd",
    "is_common_stock",
)


def _create_table(conn: sqlite3.Connection) -> None:
    """创建 ``hk_metadata`` 表（不含任何列默认值）。"""
    cols_sql = ",\n            ".join(f"{name} {typ}" for name, typ in _COLUMNS.items())
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS hk_metadata (
            ticker TEXT PRIMARY KEY,
            {cols_sql},
            updated_at TEXT,
            financial_updated_at TEXT,
            profile_updated_at TEXT
        )
        """
    )


def _rebuild_without_defaults(conn: sqlite3.Connection) -> None:
    """重建表以移除历史遗留的 ``DEFAULT 0`` 默认值。

    旧版建表语句给布尔/数值列加了 ``DEFAULT 0``，导致「未写入」被读成
    「明确不满足」（如 ``clean_audit_2y=0``）。三态语义要求未知必须是 NULL，
    因此在检测到列默认值时重建表并保留已有数据。

    Args:
        conn: SQLite 连接。
    """
    existing_cols = [row[1] for row in conn.execute("PRAGMA table_info(hk_metadata)")]
    defaulted = {row[1] for row in conn.execute("PRAGMA table_info(hk_metadata)") if row[4] is not None}
    shared = [c for c in _ALL_COLUMNS if c in existing_cols]
    conn.execute("ALTER TABLE hk_metadata RENAME TO hk_metadata_legacy")
    _create_table(conn)
    # 有默认值的列无法区分「真 0」与「未写入」，统一重置为 NULL（三态：未知）
    select_exprs = [c if c not in defaulted else "NULL" for c in shared]
    conn.execute(
        f"INSERT INTO hk_metadata ({', '.join(shared)}) "
        f"SELECT {', '.join(select_exprs)} FROM hk_metadata_legacy"
    )
    conn.execute("DROP TABLE hk_metadata_legacy")
    logger.info("hk_metadata 已重建：移除遗留 DEFAULT 默认值（默认值列重置为 NULL）")


def ensure_hk_metadata_table(conn: sqlite3.Connection) -> None:
    """幂等创建/补齐 ``hk_metadata`` 表。

    使用 ``CREATE TABLE IF NOT EXISTS`` 建表，对已存在的旧表做列补齐
    （``ALTER TABLE ADD COLUMN``），并在检测到误导性列默认值时重建表，
    保证新旧数据库都能读写同一套字段且三态语义一致。

    Args:
        conn: SQLite 连接。
    """
    _create_table(conn)

    info = list(conn.execute("PRAGMA table_info(hk_metadata)"))
    if any(row[4] is not None for row in info):
        _rebuild_without_defaults(conn)
        info = list(conn.execute("PRAGMA table_info(hk_metadata)"))

    existing = {row[1] for row in info}
    all_defs = {**_COLUMNS, **_TIMESTAMP_COLUMNS}
    for name, typ in all_defs.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE hk_metadata ADD COLUMN {name} {typ}")
            logger.debug(f"hk_metadata 补齐列: {name}")
    conn.commit()


def _normalize_value(column: str, value: Any) -> Any:  # 值类型由列语义决定，Any 为合理
    """把 Python 值规范化为可写入 SQLite 的类型（布尔转 0/1，未知保持 None）。"""
    if value is None:
        return None
    if column in _BOOL_COLUMNS:
        return int(bool(value))
    return value


def upsert_hk_metadata(
    conn: sqlite3.Connection, record: dict[str, Any]  # 记录值类型混杂，Any 为合理
) -> None:
    """按键增量插入/更新单只港股的元数据。

    只更新 ``record`` 中实际出现的列；未提供的列保持原值不变
    （避免「只刷新行情」时把财务字段写成 NULL）。

    Args:
        conn: SQLite 连接。
        record: 元数据字典，至少含 ``ticker``；其余键须在表结构内。
    """
    ensure_hk_metadata_table(conn)
    ticker = record.get("ticker")
    if not ticker:
        raise ValueError("hk_metadata 记录缺少 ticker")

    writable = {**_COLUMNS, **_TIMESTAMP_COLUMNS}
    keys = [k for k in record if k in writable]
    if not keys:
        return

    columns = ["ticker", *keys]
    values = [ticker, *(_normalize_value(k, record[k]) for k in keys)]
    placeholders = ", ".join(["?"] * len(columns))
    update_clause = ", ".join(f"{k}=excluded.{k}" for k in keys)
    if "updated_at" not in keys:
        update_clause += ", updated_at=datetime('now', 'localtime')"

    conn.execute(
        f"INSERT INTO hk_metadata ({', '.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT(ticker) DO UPDATE SET {update_clause}",
        values,
    )


def _row_to_dict(row: tuple[Any, ...]) -> dict[str, Any]:
    """把查询行转换为带三态布尔/None 数值的字典。"""
    data = dict(zip(_ALL_COLUMNS, row, strict=False))
    for col in _BOOL_COLUMNS:
        raw = data.get(col)
        data[col] = None if raw is None else bool(raw)
    for col in _INT_COLUMNS:
        raw = data.get(col)
        data[col] = None if raw is None else int(raw)
    return data


def read_hk_metadata(
    conn: sqlite3.Connection, tickers: list[str] | None = None
) -> dict[str, dict[str, Any]]:
    """批量读取港股元数据。

    Args:
        conn: SQLite 连接。
        tickers: 指定 ticker 列表（形如 ``hk.00700``）；``None`` 读取全表。

    Returns:
        ``{ticker: {列名: 值}}``；表不存在返回空字典。
    """
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='hk_metadata'"
    ).fetchone()
    if not exists:
        return {}

    select_cols = ", ".join(_ALL_COLUMNS)
    if tickers is None:
        rows = conn.execute(f"SELECT {select_cols} FROM hk_metadata").fetchall()
    elif not tickers:
        return {}
    else:
        placeholders = ",".join(["?"] * len(tickers))
        rows = conn.execute(
            f"SELECT {select_cols} FROM hk_metadata WHERE ticker IN ({placeholders})",
            tickers,
        ).fetchall()
    return {row[0]: _row_to_dict(row) for row in rows}


def is_stale(updated_at: str | None, max_age_days: int) -> bool:
    """判断时间戳是否超过 TTL（天）。

    Args:
        updated_at: ``YYYY-MM-DD HH:MM:SS`` 时间戳；``None`` 视为过期。
        max_age_days: 允许的最大天数；``<=0`` 表示永不过期。

    Returns:
        True 表示需要刷新。
    """
    if max_age_days <= 0:
        return False
    if not updated_at:
        return True
    from datetime import datetime

    try:
        ts = datetime.strptime(str(updated_at)[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return True
    return (datetime.now() - ts).days >= max_age_days


def is_stale_hours(updated_at: str | None, max_age_hours: float) -> bool:
    """判断时间戳是否超过 TTL（小时）。

    Args:
        updated_at: ``YYYY-MM-DD HH:MM:SS`` 时间戳；``None`` 视为过期。
        max_age_hours: 允许的最大小时数；``<=0`` 表示永不过期。

    Returns:
        True 表示需要刷新。
    """
    if max_age_hours <= 0:
        return False
    if not updated_at:
        return True
    from datetime import datetime

    try:
        ts = datetime.strptime(str(updated_at)[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return True
    return (datetime.now() - ts).total_seconds() >= max_age_hours * 3600


def has_financial_data(record: dict[str, Any] | None) -> bool:
    """判断记录是否已有可用财务字段（6 个核心指标至少 4 个非空）。"""
    if not record:
        return False
    filled = sum(1 for col in FINANCIAL_COLUMNS if record.get(col) is not None)
    return filled >= 4


def has_profile_data(record: dict[str, Any] | None) -> bool:
    """判断记录是否已有可用档案字段（上市信息/港股通/标的类型）。"""
    if not record:
        return False
    filled = sum(1 for col in PROFILE_COLUMNS if record.get(col) is not None)
    return filled >= 3
