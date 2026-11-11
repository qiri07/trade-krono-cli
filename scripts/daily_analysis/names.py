#!/usr/bin/env python3
"""daily_analysis 股票名称解析模块。

进程级名称预加载与逐只兜底查询，名称↔代码映射持久化到本地 SQLite
（``pipeline_cache.db`` 的 ``stock_name_cache`` 表）：

- ``_preload_stock_names`` 一次性拉取全市场名称表填充 STOCK_NAMES，并种入本地缓存
- ``_get_stock_name`` 优先从本地缓存读（命中不联网），缺失再走内存映射 / baostock 逐只查询
- 查到新名称会写回本地缓存，实现「每日仅补漏，命中即本地使用」
"""

from __future__ import annotations

import threading

from loguru import logger

from scripts._utils import CACHE_DB, STOCK_NAMES, get_sqlite_conn
from scripts.stock_name_cache import (
    batch_write_stock_name_cache,
    ensure_stock_name_table,
    get_cached_stock_name,
)

# 进程级名称预加载状态：避免 20 并发扫描时每个线程各自触发 baostock 网络查询，
# 且查询失败的 ticker 不再重试（失败记忆），防止单只挂起拖死整个并发池。
_NAME_PRELOAD_LOCK = threading.Lock()
_NAME_PRELOAD_DONE = False
_NAME_FAILED: set[str] = set()

# DB 写入锁：sqlite 单连接串行化，避免并发写冲突
_NAME_DB_LOCK = threading.Lock()


def _preload_stock_names(timeout: float = 180.0) -> None:
    """进程启动时一次性拉取全市场名称表填充 STOCK_NAMES，并种入本地缓存。

    baostock ``query_stock_basic(code="")`` 一次返回全表（约 9000 行），
    远优于逐只网络查询。带进程级锁，多线程并发调用仅首次实际拉取。
    拉取完成后将名称映射批量写入 ``stock_name_cache`` 表，作为后续每日
    查询的本地种子（命中即本地使用，不联网）。

    Parameters
    ----------
    timeout : float
        单线程拉取超时秒数，超时则放弃预加载、回退逐只查询。
    """
    global _NAME_PRELOAD_DONE
    with _NAME_PRELOAD_LOCK:
        if _NAME_PRELOAD_DONE:
            return
        try:
            from trade_krono_cli.data_providers import get_data_factory

            factory = get_data_factory()
            provider = factory.get_provider("baostock")
            if provider is None:
                _NAME_PRELOAD_DONE = True
                return
            result_box: list[list[list[str]]] = []

            def _do_fetch() -> None:
                # 字段顺序：0=code, 1=code_name, 2=ipoDate, 3=outDate, ...
                rows = provider._query_stock_basic("")  # type: ignore[attr-defined]
                result_box.append(rows)

            t = threading.Thread(target=_do_fetch, daemon=True)
            t.start()
            t.join(timeout=timeout)
            if t.is_alive():
                logger.warning(f"⚠️ 名称预加载超时({timeout}s)，回退逐只查询")
                _NAME_PRELOAD_DONE = True
                return
            if result_box:
                prefixed: dict[str, str] = {}
                for row in result_box[0]:
                    code = str(row[0]).split(".", 1)[-1] if row and row[0] else ""
                    name = (row[1].strip() if len(row) > 1 else "")
                    if code and name:
                        STOCK_NAMES.setdefault(code, name)
                        # baostock 返回的 code 为裸 6 位 A 股代码，补交易所前缀
                        bare = code
                        prefix = "sh" if bare.startswith(("6", "5")) else "sz"
                        prefixed[f"{prefix}.{bare}"] = name
                # 种入本地 SQLite 名称缓存（幂等 upsert），作为后续每日查询的本地种子
                if prefixed:
                    conn = get_sqlite_conn(CACHE_DB)
                    try:
                        ensure_stock_name_table(conn)
                        batch_write_stock_name_cache(conn, list(prefixed.items()))
                        logger.info(
                            f"💾 名称映射已种入本地缓存：{len(prefixed)} 条 → stock_name_cache"
                        )
                    finally:
                        conn.close()
                        conn.close()
                logger.info(
                    f"✅ 名称预加载完成：全表 {len(result_box[0])} 条 → STOCK_NAMES {len(STOCK_NAMES)} 条"
                )
        except Exception as e:
            logger.warning(f"⚠️ 名称预加载失败，回退逐只查询: {e}")
        finally:
            _NAME_PRELOAD_DONE = True


def _get_stock_name_from_db(ticker: str) -> str:
    """从本地 SQLite 名称缓存读取股票名称。

    命中时直接返回，不联网，实现每日流程「命中即本地使用」。

    Parameters
    ----------
    ticker : str
        带交易所前缀的 ticker，如 ``sh.600519``。

    Returns
    -------
    str
        股票名称，未命中返回空字符串。
    """
    try:
        conn = get_sqlite_conn(CACHE_DB)
        try:
            ensure_stock_name_table(conn)
            name = get_cached_stock_name(conn, ticker)
            # 仅当查到合法字符串名称时才视为命中；mock 或损坏数据（bytes 等）
            # 不应被当作名称，避免在测试/异常环境下误判。
            if isinstance(name, str) and name:
                return name
            return ""
        finally:
            conn.close()
    except Exception as e:
        logger.debug(f"{ticker} 从本地名称缓存读取失败: {e}")
        return ""


def _write_stock_names_to_db(names: dict[str, str]) -> None:
    """把名称映射批量写入本地 SQLite 名称缓存（幂等 upsert）。

    Parameters
    ----------
    names : dict[str, str]
        {ticker: name} 映射，ticker 带交易所前缀。
    """
    if not names:
        return
    try:
        conn = get_sqlite_conn(CACHE_DB)
        try:
            ensure_stock_name_table(conn)
            batch_write_stock_name_cache(conn, list(names.items()))
            logger.debug(f"💾 名称映射已写回本地缓存：{len(names)} 条")
        finally:
            conn.close()
    except Exception as e:
        logger.debug(f"写回本地名称缓存失败: {e}")


def _get_stock_name(ticker: str) -> str:
    """获取股票名称。

    查询优先级：
    1. 本地 SQLite 缓存（命中直接返回，不联网）
    2. 进程内 STOCK_NAMES 映射
    3. baostock 逐只兜底查询（超时+失败记忆），查到后写回本地缓存

    调用前请先 ``_preload_stock_names()`` 一次性拉全表；此函数仅处理预加载
    仍缺失的少量 ticker，并对单只查询施加 30s 超时，避免并发池被网络挂起拖死。

    Parameters
    ----------
    ticker : str
        股票代码（带交易所前缀，如 sh.600519）

    Returns
    -------
    str
        股票名称，无法获取时返回空字符串。
    """
    code = ticker.split(".", 1)[1] if "." in ticker else ticker

    # 1. 优先本地 SQLite 缓存：命中即本地使用，不联网
    name = _get_stock_name_from_db(ticker)
    if name:
        return name

    # 2. 进程内映射
    name = STOCK_NAMES.get(code, "")
    if name:
        return name

    # 3. 预加载已完成后仍缺失的 ticker，视为无名称映射，不再联网，避免拖死线程池
    if ticker in _NAME_FAILED:
        return ""
    if _NAME_PRELOAD_DONE:
        return ""

    try:
        from trade_krono_cli.data_providers import get_data_factory

        factory = get_data_factory()
        provider = factory.get_provider("baostock")
        if provider is None:
            _NAME_FAILED.add(ticker)
            return ""
        result_box: list[list[list[str]]] = []

        def _do_fetch() -> None:
            # _query_stock_basic 内部处理登录，字段顺序：code, code_name, ipoDate, outDate, ...
            rows = provider._query_stock_basic(ticker)  # type: ignore[attr-defined]
            result_box.append(rows)

        t = threading.Thread(target=_do_fetch, daemon=True)
        t.start()
        t.join(timeout=30)
        if t.is_alive():
            _NAME_FAILED.add(ticker)
            logger.debug(f"{ticker} 名称查询超时(30s)，跳过")
            return ""
        if result_box and result_box[0] and len(result_box[0][0]) > 1:
            fetched_name = result_box[0][0][1].strip()
            if fetched_name:
                STOCK_NAMES[code] = fetched_name
                # 写回本地缓存，下次直接命中不联网
                _write_stock_names_to_db({ticker: fetched_name})
                return fetched_name
        _NAME_FAILED.add(ticker)
    except Exception as e:
        _NAME_FAILED.add(ticker)
        logger.debug(f"{ticker} 名称查询失败: {e}")
    return ""
