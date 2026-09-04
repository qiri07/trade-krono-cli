"""utils/helpers.py — 共享工具函数。

包含时间、数据校验、安全解析等通用工具函数。
"""

from __future__ import annotations

import pandas as pd
from loguru import logger


def next_business_days(last_date: str, n: int) -> list[pd.Timestamp]:
    """生成后续 n 个工作日（不含周末近似）。"""
    from pandas.tseries.offsets import BDay

    start = pd.Timestamp(last_date) + BDay(1)
    return [start + BDay(i) for i in range(n)]


def validate_data_freshness(
    df: pd.DataFrame,
    eval_date: str,
    ticker: str,
    max_gap_trading_days: int = 10,
) -> None:
    """校验 K 线数据的最后交易日与评估日期的间隔。

    如果数据末尾距离评估日期超过 max_gap_trading_days 个交易日，
    说明股票在评估日前长时间停牌，不应参与预测。

    Raises
    ------
    RuntimeError : 数据过旧或不存在

    """
    if "timestamps" not in df.columns:
        msg = f"数据格式异常，缺少 timestamps 列: {ticker}"
        raise RuntimeError(msg)

    last_ts = pd.to_datetime(df["timestamps"].iloc[-1])
    eval_ts = pd.to_datetime(eval_date)

    if last_ts > eval_ts:
        msg = f"数据未来化: {ticker} 数据截止 {last_ts.date()} 晚于评估日期 {eval_ts.date()}"
        raise RuntimeError(msg)

    trading_days_gap = len(pd.bdate_range(start=last_ts, end=eval_ts)) - 1

    if trading_days_gap > max_gap_trading_days:
        msg = (
            f"数据过旧: {ticker} 最后交易日 {last_ts.date()} 与评估日 {eval_ts.date()} "
            f"相差 {trading_days_gap} 个交易日（阈值 {max_gap_trading_days}），"
            f"疑似停牌或退市"
        )
        raise RuntimeError(msg)

    logger.debug(
        f"✅ 数据新鲜度校验通过: {ticker} 最后交易日={last_ts.date()}, "
        f"与评估日间隔 {trading_days_gap} 个交易日",
    )


def safe_float(value: str, default: float | None = None) -> float | None:
    """安全地将字符串解析为 float，失败时返回 default。

    腾讯行情接口可能返回空串、'--' 等占位符，不应让整个函数失败。
    """
    if not value:
        return default
    try:
        f = float(value)
        return f if not (f != f or f == float("inf") or f == float("-inf")) else default
    except (ValueError, TypeError):
        return default


# 别名：保持与旧代码的兼容性
_safe_float = safe_float
