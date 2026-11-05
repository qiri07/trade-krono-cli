#!/usr/bin/env python3
"""daily_analysis K 线读取与外部补拉模块。

三级回退的数据获取支撑：缓存日期范围查询 → 全量缓存读取 → 外部源拉取并写缓存。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
from loguru import logger

from scripts._utils import get_sqlite_conn
from scripts.daily_analysis.whitelist import _get_cache_db
from trade_krono_cli.cache import get_cache
from trade_krono_cli.data_providers import get_data_factory


def get_kline(ticker: str, start: str | None = None, end: str | None = None) -> pd.DataFrame | None:
    """从缓存读取 K 线数据（支持日期范围过滤）。

    Parameters
    ----------
    ticker : str
        股票代码（带交易所前缀，如 sh.600519）
    start : str | None
        起始日期 YYYY-MM-DD，为空时不限定下限
    end : str | None
        结束日期 YYYY-MM-DD，为空时不限定上限

    Returns
    -------
    pd.DataFrame | None
        K 线 DataFrame（含 timestamps/open/high/low/close/volume 列），无数据时返回 None
    """
    cache = get_cache()
    kline_cache = cache  # Cache 实例本身有 get_kline/set_kline 方法
    # ETF 无复权概念，缓存统一按 adjustflag='1' 存储，与普通股票一致
    if start is not None and end is not None:
        return kline_cache.get_kline(ticker, start, end, freq="d", adjustflag="1")
    # 无日期限制：直接读原始行

    conn = get_sqlite_conn(_get_cache_db())
    row = conn.execute("SELECT data FROM kline_cache WHERE ticker = ?", (ticker,)).fetchone()
    conn.close()
    if not row:
        return None
    try:
        from io import BytesIO

        df = pd.read_pickle(BytesIO(row[0]))
    except Exception as e:
        logger.warning(f"{ticker} 数据读取失败: {e}")
        return None
    return df


def _fetch_and_save_kline(ticker: str, end_date: str) -> pd.DataFrame | None:
    """拉取 K 线并写入本地缓存，失败时静默返回 None。

    Parameters
    ----------
    ticker : str
        股票代码（带交易所前缀）
    end_date : str
        查询结束日期 YYYY-MM-DD

    Returns
    -------
    pd.DataFrame | None
        拉取到的 K 线 DataFrame，失败时返回 None
    """
    try:
        factory = get_data_factory()
        # 拉取近一年历史数据（足够计算 MA60）
        start_date = (datetime.strptime(end_date, "%Y-%m-%d") - timedelta(days=365)).strftime(
            "%Y-%m-%d"
        )
        kline_data = factory.fetch_kline(
            ticker, start_date, end_date, frequency="d", adjustflag="1"
        )
        if kline_data is None or kline_data.is_empty:
            logger.warning(f"{ticker} 外部数据源亦无数据")
            return None
        df = kline_data.to_dataframe()
        if df is None or len(df) == 0:
            return None
        cache = get_cache()
        ts_col = "timestamps" if "timestamps" in df.columns else "date"
        df[ts_col] = pd.to_datetime(df[ts_col])
        df = df.sort_values(ts_col).reset_index(drop=True)
        cache.set_kline(
            ticker,
            df[ts_col].iloc[0].strftime("%Y-%m-%d"),
            df[ts_col].iloc[-1].strftime("%Y-%m-%d"),
            freq="d",
            df=df,
            ttl=0.0,
            adjustflag="1",
        )
        logger.info(f"{ticker} 已从外部源拉取并缓存 {len(df)} 条记录")
        return df
    except Exception as e:
        logger.warning(f"{ticker} 拉取失败: {e}")
        return None
