"""
data_fetcher — K 线数据获取封装。

从 baostock 拉取 K 线，支持缓存和复权因子配置。
"""
from __future__ import annotations

from typing import Optional

import pandas as pd
from loguru import logger

from trade_krono_cli.data import fetch_lookback, fetch_realtime_quote
from trade_krono_cli.constraints_config import ConstraintConfig
from trade_krono_cli.errors import DataError


def fetch_stock_data(
    ticker: str,
    eval_date: str,
    lookback: int = 400,
    adjustflag: str = "1",
    use_cache: bool = True,
) -> pd.DataFrame:
    """
    获取单只股票的 K 线数据。

    Parameters
    ----------
    ticker : 股票代码（如 sh.600519）
    eval_date : 评估日期
    lookback : 回看长度
    adjustflag : 复权因子（"1"=前复权，默认）
    use_cache : 是否使用缓存

    Returns
    -------
    DataFrame with columns: timestamps, open, high, low, close, volume, amount
    """
    df = fetch_lookback(
        ticker, eval_date,
        lookback=lookback,
        frequency="d",
        adjustflag=adjustflag,
        use_cache=use_cache,
    )
    logger.debug(f"📊 {ticker} K 线就绪: {len(df)} 行")
    return df


def fetch_stock_quote(
    ticker: str,
) -> dict:
    """
    获取实时估值数据（腾讯财经）。

    Returns
    -------
    {price, pe, pb, market_cap, turnover} 或 {}
    """
    return fetch_realtime_quote(ticker)


def prepare_kline_batch(
    tickers: list[str],
    eval_date: str,
    lookback: int = 400,
    adjustflag: str = "1",
    use_cache: bool = True,
) -> dict[str, pd.DataFrame]:
    """
    批量准备 K 线数据。

    Returns
    -------
    {ticker: kline_df} 字典
    """
    result = {}
    for tk in tickers:
        try:
            result[tk] = fetch_stock_data(
                tk, eval_date, lookback=lookback,
                adjustflag=adjustflag, use_cache=use_cache,
            )
        except DataError as e:
            logger.warning(f"⚠️  K 线获取失败 {tk}: {e}")
        except Exception as e:
            logger.warning(f"⚠️  K 线获取异常 {tk}: {str(e)[:200]}")
    return result
