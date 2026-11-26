"""scripts._utils.universe — 统一股票池过滤引擎。

提供从缓存数据库读取 ticker 列表、执行 ST/北交所/港股/ETF/PE/PB/市值过滤等功能。
各选股/扫描脚本在扫描全市场前应统一调用 :func:`filter_stock_universe`，保证口径一致。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path

from scripts._utils.metadata import exclusion_reason
from scripts._utils.ticker import is_bse_or_hk, is_etf_ticker


def get_sqlite_conn(db_path: Path | str | None = None, readonly: bool = False) -> sqlite3.Connection:
    """获取一个配置好的 SQLite 连接。

    使用与 Cache 类相同的 WAL 模式和 busy_timeout 设置，确保多线程环境下
    并发访问时不会出现 database is locked 错误。

    Args:
        db_path: 数据库文件路径，缺省用 metadata 模块的缓存路径。
        readonly: 是否以只读模式打开，默认 False。

    Returns:
        配置好的 sqlite3.Connection 实例。
    """
    if db_path is None:
        from scripts._utils.metadata import get_cache_db_path

        db_path = get_cache_db_path()
    if readonly:
        uri = f"file:{db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=30.0)
    else:
        conn = sqlite3.connect(str(db_path), check_same_thread=False, timeout=30.0)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
    except Exception:
        pass  # 测试中可能 mock 连接对象，不支持 PRAGMA
    return conn


def get_all_tickers() -> list[str]:
    """从 SQLite 缓存数据库读取所有不重复的 ticker 列表。

    Returns:
        按字母序排序的 ticker 列表。
    """
    from scripts._utils.metadata import get_cache_db_path

    conn = get_sqlite_conn(get_cache_db_path())
    tickers = [r[0] for r in conn.execute("SELECT DISTINCT ticker FROM kline_cache").fetchall()]
    conn.close()
    return sorted(tickers)


def get_astock_tickers(
    db_path: Path, logger_fn: Callable[[str], None] | None = None
) -> list[str]:
    """从指定缓存数据库读取全部 ticker，并排除北交所与港股。

    保留 A 股 + ETF（ETF 以纯技术面参与各筛选线）。

    Args:
        db_path: kline_cache 数据库路径。
        logger_fn: 可选日志函数（如 ``logger.info``），用于输出扫描统计。

    Returns:
        排除北交所/港股后的 ticker 列表（按字母序）。
    """
    conn = get_sqlite_conn(db_path)
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT ticker FROM kline_cache ORDER BY ticker")
    all_tickers = [r[0] for r in cur.fetchall()]
    conn.close()

    astock_tickers = [t for t in all_tickers if not is_bse_or_hk(t)]
    if logger_fn is not None:
        etf_count = sum(1 for t in astock_tickers if is_etf_ticker(t))
        logger_fn(
            f"📊 全市场扫描：共 {len(all_tickers)} 只，排除北交所/港股后 {len(astock_tickers)} 只"
            f"（含ETF {etf_count} 只，纯技术面参与）"
        )
    return astock_tickers


def filter_stock_universe(
    tickers: list[str],
    metadata: dict[str, dict[str, object]] | None = None,
    *,
    exclude_st: bool = True,
    exclude_bse: bool = True,
    exclude_hk: bool = True,
    exclude_etf: bool = False,
    max_pe: float | None = None,
    min_pb: float | None = None,
    min_market_cap: float | None = None,
) -> list[str]:
    """统一股票池过滤引擎。

    各选股/扫描脚本在扫描全市场前应统一调用本函数，避免重复实现
    ST / 北交所 / 港股 / ETF / PE / PB / 市值过滤逻辑，保证口径一致。

    Args:
        tickers: 带前缀 ticker 列表，如 ``["sh.600519", "hk.00700", "bj.920001"]``。
        metadata: :func:`load_metadata_index` 返回的元数据索引。为 ``None`` 时
            只做代码/前缀层面的过滤，不做 ST / 估值 / 市值过滤。
        exclude_st: 是否剔除 ST 股（优先读 ``is_st`` 标记，否则按名称关键词判断）。
        exclude_bse: 是否剔除北交所（``bj.`` 前缀或 920 开头裸代码）。
        exclude_hk: 是否剔除港股（``hk.`` 前缀）。
        exclude_etf: 是否剔除 ETF（``sh.5x`` / ``sh.58x`` / ``sz.15x`` 等前缀）。
        max_pe: PE 上限。``pe >= max_pe`` 或 ``pe <= 0``（亏损/无效）时剔除。
        min_pb: PB 下限，``pb < min_pb`` 时剔除（用于资不抵债过滤）。
        min_market_cap: 最小市值（亿元），低于此值时剔除。

    Returns:
        过滤后的 ticker 列表，保持原始顺序。
    """
    result: list[str] = []

    for ticker in tickers:
        if (
            exclusion_reason(
                ticker,
                metadata,
                exclude_st=exclude_st,
                exclude_bse=exclude_bse,
                exclude_hk=exclude_hk,
                exclude_etf=exclude_etf,
            )
            is not None
        ):
            continue

        if metadata is not None:
            meta = _lookup_meta(metadata, ticker)
            if max_pe is not None:
                pe = meta.get("pe_ttm")
                if pe is not None:
                    try:
                        pe_val = float(str(pe))
                        if pe_val <= 0 or pe_val >= max_pe:
                            continue
                    except (TypeError, ValueError):
                        pass
            if min_pb is not None:
                pb = meta.get("pb")
                if pb is not None:
                    try:
                        if float(str(pb)) < min_pb:
                            continue
                    except (TypeError, ValueError):
                        pass
            if min_market_cap is not None:
                cap = meta.get("market_cap")
                if cap is not None:
                    try:
                        if float(str(cap)) < min_market_cap:
                            continue
                    except (TypeError, ValueError):
                        pass

        result.append(ticker)

    return result


def _lookup_meta(
    metadata: dict[str, dict[str, object]] | None, ticker: str
) -> dict[str, object]:
    """在元数据索引中查单只票，兼容带前缀与裸代码两种键。"""
    if metadata is None:
        return {}
    from scripts._utils.metadata import (
        normalize_ticker_key,
        strip_ticker_prefix,
    )

    normalized = normalize_ticker_key(ticker)
    bare = strip_ticker_prefix(normalized)
    for key in (ticker, normalized, bare):
        value = metadata.get(key)
        if isinstance(value, dict):
            return value
    return {}
