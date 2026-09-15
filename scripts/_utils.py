#!/usr/bin/env python3
"""scripts 目录共享工具函数。

提供数据库访问、股票列表读取、Provider 链构建等通用功能，
避免各同步脚本重复定义相同逻辑。

用法：
  from scripts._utils import get_all_tickers, get_provider_chain, CACHE_DB
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

CACHE_DB = Path("outputs/cache/pipeline_cache.db")


def get_all_tickers() -> list[str]:
    """从 SQLite 缓存数据库读取所有不重复的 ticker 列表。

    Returns:
        按字母序排序的 ticker 列表。
    """
    conn = sqlite3.connect(str(CACHE_DB))
    tickers = [r[0] for r in conn.execute("SELECT DISTINCT ticker FROM kline_cache").fetchall()]
    conn.close()
    return sorted(tickers)


def get_provider_chain(ticker: str) -> list[str]:
    """根据股票类型返回 Provider 优先级链。

    Args:
        ticker: 股票代码，支持 sh.600519 / sz.000001 / bj.920001 格式。

    Returns:
        Provider 名称列表，按优先级降序排列。
    """
    if ticker.startswith("bj."):
        return ["tonghuashun", "baostock"]
    elif ticker.startswith("sh.") or ticker.startswith("sz."):
        return ["tonghuashun", "baostock", "mootdx"]
    return ["baostock", "tonghuashun"]
