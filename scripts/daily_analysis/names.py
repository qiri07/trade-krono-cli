#!/usr/bin/env python3
"""daily_analysis 股票名称解析模块。

进程级名称预加载与逐只兜底查询：
- ``_preload_stock_names`` 一次性拉取全市场名称表填充 STOCK_NAMES
- ``_get_stock_name`` 单只名称查询（带超时 + 失败记忆）
"""

from __future__ import annotations

import threading

from loguru import logger

from scripts._utils import STOCK_NAMES

# 进程级名称预加载状态：避免 20 并发扫描时每个线程各自触发 baostock 网络查询，
# 且查询失败的 ticker 不再重试（失败记忆），防止单只挂起拖死整个并发池。
_NAME_PRELOAD_LOCK = threading.Lock()
_NAME_PRELOAD_DONE = False
_NAME_FAILED: set[str] = set()


def _preload_stock_names(timeout: float = 180.0) -> None:
    """进程启动时一次性拉取全市场股票名称，填充全局 STOCK_NAMES。

    baostock ``query_stock_basic(code="")`` 一次返回全表（约 9000 行），
    远优于逐只网络查询。带进程级锁，多线程并发调用仅首次实际拉取。

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
                for row in result_box[0]:
                    code = str(row[0]).split(".", 1)[-1] if row and row[0] else ""
                    name = (row[1].strip() if len(row) > 1 else "")
                    if code and name:
                        STOCK_NAMES.setdefault(code, name)
                logger.info(
                    f"✅ 名称预加载完成：全表 {len(result_box[0])} 条 → STOCK_NAMES {len(STOCK_NAMES)} 条"
                )
        except Exception as e:
            logger.warning(f"⚠️ 名称预加载失败，回退逐只查询: {e}")
        finally:
            _NAME_PRELOAD_DONE = True


def _get_stock_name(ticker: str) -> str:
    """获取股票名称，优先查本地映射，其次通过 baostock 动态查询（带超时+失败记忆）。

    调用前请先 ``_preload_stock_names()`` 一次性拉全表；此函数仅处理预加载
    仍缺失的少量 ticker，并对单只查询施加 30s 超时，避免并发池被网络挂起拖死。

    Parameters
    ----------
    ticker : str
        股票代码（带交易所前缀，如 sh.600519）

    Returns
    -------
    str
        股票名称，无法获取时返回空字符串
    """
    code = ticker.split(".", 1)[1] if "." in ticker else ticker
    name = STOCK_NAMES.get(code, "")
    if name:
        return name
    # 仅对少量映射缺失的 ticker 做逐只兜底查询；命中失败记忆则直接跳过。
    # 关键：预加载已完成后仍缺失的 ticker，视为该代码无名称映射，直接返回空，
    # 不再触发外部网络拉取，避免大并发扫描时个别 ticker 挂起拖死整个线程池。
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
                return fetched_name
        _NAME_FAILED.add(ticker)
    except Exception as e:
        _NAME_FAILED.add(ticker)
        logger.debug(f"{ticker} 名称查询失败: {e}")
    return ""
