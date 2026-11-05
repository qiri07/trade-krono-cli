"""scripts/indicators.py — 公共技术指标计算模块。

下沉各选股/扫描脚本中重复的技术指标实现（MA、MACD DIF、金叉、多头排列等），
统一由本模块提供 pandas-based 计算，避免多脚本重复造轮子。

所有函数均为纯计算，不涉及网络 I/O 和数据库读写，便于单元测试。
"""

from __future__ import annotations

import pandas as pd  # noqa: TC002  (pandas 用于运行时计算，非仅类型注解)

from scripts._utils import (
    ST_NAME_KEYWORDS_MINIMAL,
    is_bse_or_hk,
    is_etf_ticker,
    normalize_ticker_key,
)

# 默认均线周期（与 double_ma_golden_cross / macd_ma_screener 保持一致）
MA_FAST: int = 5
MA_SLOW: int = 13
MACD_FAST: int = 12
MACD_SLOW: int = 26


def calc_ma(series: pd.Series, window: int) -> pd.Series:
    """计算简单移动平均（SMA）。

    与 pandas ``Series.rolling(window).mean()`` 语义一致，前 ``window-1`` 个值为 NaN。

    Parameters
    ----------
    series : pd.Series
        价格序列（如收盘价）。
    window : int
        移动平均窗口。

    Returns
    -------
    pd.Series
        与输入等长的 SMA 序列，前 ``window-1`` 个值为 NaN。
    """
    return series.rolling(window).mean()


def calc_macd_dif(series: pd.Series, fast: int = MACD_FAST, slow: int = MACD_SLOW) -> float:
    """计算 MACD DIF 线的最新值（>0 表示 0 轴之上）。

    DIF = EMA(fast) - EMA(slow)。仅返回最新一个数据点的 DIF 值。

    Parameters
    ----------
    series : pd.Series
        价格序列（如收盘价），长度需 >= slow。
    fast : int
        EMA 快线周期，默认 12。
    slow : int
        EMA 慢线周期，默认 26。

    Returns
    -------
    float
        最新 DIF 值；数据不足时返回 float("nan")。
    """
    if len(series) < slow:
        return float("nan")
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    return float((ema_fast - ema_slow).iloc[-1])


def calc_macd_dif_series(
    series: pd.Series, fast: int = MACD_FAST, slow: int = MACD_SLOW
) -> pd.Series:
    """计算 MACD DIF 线的完整序列（与输入等长）。

    Parameters
    ----------
    series : pd.Series
        价格序列。
    fast : int
        EMA 快线周期，默认 12。
    slow : int
        EMA 慢线周期，默认 26。

    Returns
    -------
    pd.Series
        与输入等长的 DIF 序列。
    """
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    return ema_fast - ema_slow


def is_golden_cross(closes: pd.Series, fast: int = MA_FAST, slow: int = MA_SLOW) -> bool:
    """判断是否发生 MA 快线上穿慢线金叉（已收盘 bar 处）。

    判定条件：前一根 MA_fast <= MA_slow 且 当前 MA_fast > MA_slow。

    Parameters
    ----------
    closes : pd.Series
        收盘价序列，长度需 >= slow + 1。
    fast : int
        快线周期，默认 5。
    slow : int
        慢线周期，默认 13。

    Returns
    -------
    bool
        是否发生金叉；数据不足时返回 False。
    """
    if len(closes) < slow + 1:
        return False
    ma_fast = calc_ma(closes, fast)
    ma_slow = calc_ma(closes, slow)
    i = -1  # 已收盘的最后一根
    try:
        return bool(
            ma_fast.iloc[i] > ma_slow.iloc[i]
            and ma_fast.iloc[i - 1] <= ma_slow.iloc[i - 1]
        )
    except (IndexError, TypeError):
        return False


def is_multi_head(closes: pd.Series) -> bool:
    """判断是否为多头排列：close > MA5 > MA10 > MA20（已收盘 bar 处）。

    Parameters
    ----------
    closes : pd.Series
        收盘价序列，长度需 >= 21。

    Returns
    -------
    bool
        是否为多头排列；数据不足时返回 False。
    """
    if len(closes) < 21:
        return False
    px = closes.iloc[-1]
    ma5 = calc_ma(closes, 5).iloc[-1]
    ma10 = calc_ma(closes, 10).iloc[-1]
    ma20 = calc_ma(closes, 20).iloc[-1]
    try:
        return bool(px > ma5 > ma10 > ma20)
    except TypeError:
        return False


def is_macd_rising(
    series: pd.Series, fast: int = MACD_FAST, slow: int = MACD_SLOW
) -> bool:
    """判断 MACD DIF 是否在 0 轴之上且上升（已收盘 bar 处）。

    判定条件：DIF[-1] > 0 且 DIF[-1] > DIF[-2]。

    Parameters
    ----------
    series : pd.Series
        价格序列，长度需 >= slow + 1。
    fast : int
        EMA 快线周期，默认 12。
    slow : int
        EMA 慢线周期，默认 26。

    Returns
    -------
    bool
        是否 DIF>0 且上升；数据不足时返回 False。
    """
    if len(series) < slow + 1:
        return False
    dif = calc_macd_dif_series(series, fast=fast, slow=slow)
    try:
        return bool(dif.iloc[-1] > 0 and dif.iloc[-1] > dif.iloc[-2])
    except (IndexError, TypeError):
        return False


def exclude_st_bse_hk(tickers: list[str]) -> list[str]:
    """过滤掉 ST 股、北交所、港股，返回剩余 A 股 ticker 列表。

    复用 :func:`scripts._utils.is_bse_or_hk` 与 :data:`scripts._utils.ST_NAME_KEYWORDS_MINIMAL`。

    Parameters
    ----------
    tickers : list[str]
        带前缀 ticker 列表，如 ["sh.600519", "hk.00700", "bj.920001"]。

    Returns
    -------
    list[str]
        过滤后的 A 股 ticker 列表（不含 ST/北交所/港股）。
    """
    result: list[str] = []
    for ticker in tickers:
        if is_bse_or_hk(ticker):
            continue
        # 获取名称判断 ST（需通过元数据或名称映射，此处仅按 ticker 前缀兜底）
        bare = normalize_ticker_key(ticker).split(".", 1)[-1]
        if any(kw in bare for kw in ST_NAME_KEYWORDS_MINIMAL):
            continue
        result.append(ticker)
    return result


def filter_etf(tickers: list[str]) -> list[str]:
    """从 ticker 列表中筛选出 ETF 代码。

    Parameters
    ----------
    tickers : list[str]
        带前缀 ticker 列表。

    Returns
    -------
    list[str]
        ETF ticker 列表。
    """
    return [t for t in tickers if is_etf_ticker(t)]


def exclude_etf(tickers: list[str]) -> list[str]:
    """从 ticker 列表中剔除 ETF 代码。

    Parameters
    ----------
    tickers : list[str]
        带前缀 ticker 列表。

    Returns
    -------
    list[str]
        非 ETF ticker 列表。
    """
    return [t for t in tickers if not is_etf_ticker(t)]
