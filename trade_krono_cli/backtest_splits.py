"""trade_krono_cli.backtest_splits — 股票拆分检测与调整。

职责（从 backtest_engine.py 提取）：
  · 检测相邻交易日价格比率异常，识别拆分事件
  · 应用拆分调整因子到价格
  · 单只股票拆分事件检测（公开接口）
"""

from __future__ import annotations


def detect_splits(
    prices: dict[str, float],
    prev_close_map: dict[str, float],
    split_ratio_threshold: float = 3.0,
    enable_split_detection: bool = True,
) -> dict[str, float]:
    """检测股票拆分（Split）。

    通过检查相邻交易日的价格比率异常变化，识别可能的股票拆分事件。
    当价格比率超过阈值（默认 3.0）或低于其倒数时，判定为可能发生拆分。

    Parameters
    ----------
    prices : dict[str, float]
        {股票代码: 当前收盘价}
    prev_close_map : dict[str, float]
        {股票代码: 前收盘价}
    split_ratio_threshold : float
        拆分判定阈值，默认 3.0。
    enable_split_detection : bool
        是否启用拆分检测，默认 True。

    Returns
    -------
    dict[str, float]
        {股票代码: 调整因子}，调整因子为 1.0 表示未发生拆分，
        否则为调整比例（如 0.5 表示 2 拆 1）。
    """
    if not enable_split_detection:
        return {}

    split_adjustments: dict[str, float] = {}
    for ticker, price in prices.items():
        prev_close = prev_close_map.get(ticker)
        if prev_close is None or prev_close <= 0 or price <= 0:
            continue
        ratio = price / prev_close
        # 检查是否发生拆分：价格异常上涨或下跌
        if ratio >= split_ratio_threshold:
            # 上涨过多，可能是反向拆分（合并股份）
            split_adjustments[ticker] = 1.0 / ratio
        elif ratio <= 1.0 / split_ratio_threshold:
            # 下跌过多，可能是正向拆分（如 2 拆 1）
            split_adjustments[ticker] = ratio
    return split_adjustments


def apply_split_adjustment(
    price: float,
    adjustment_factor: float,
) -> float:
    """应用拆分调整因子到价格。

    Parameters
    ----------
    price : float
        原始价格。
    adjustment_factor : float
        调整因子（1.0 表示无调整）。

    Returns
    -------
    float
        调整后的价格。
    """
    if adjustment_factor == 1.0:
        return price
    return price * adjustment_factor


def detect_split_for_ticker(
    ticker: str,
    price_history: list[tuple[str, float]],
    split_ratio_threshold: float = 3.0,
    enable_split_detection: bool = True,
) -> list[dict]:
    """检测单只股票的拆分事件。

    Parameters
    ----------
    ticker : str
        股票代码。
    price_history : list[tuple[str, float]]
        [(日期, 收盘价), ...] 按日期排序的价格序列。
    split_ratio_threshold : float
        拆分判定阈值，默认 3.0。
    enable_split_detection : bool
        是否启用拆分检测，默认 True。

    Returns
    -------
    list[dict]
        拆分事件列表，每项包含 date, ticker, ratio, type, adjustment_factor。
    """
    if not enable_split_detection or len(price_history) < 2:
        return []

    events: list[dict] = []
    for i in range(1, len(price_history)):
        prev_date, prev_price = price_history[i - 1]
        curr_date, curr_price = price_history[i]
        if prev_price <= 0 or curr_price <= 0:
            continue
        ratio = curr_price / prev_price
        if ratio >= split_ratio_threshold:
            events.append(
                {
                    "date": curr_date,
                    "ticker": ticker,
                    "ratio": round(ratio, 4),
                    "type": "reverse_split",  # 反向拆分
                    "adjustment_factor": round(1.0 / ratio, 4),
                }
            )
        elif ratio <= 1.0 / split_ratio_threshold:
            events.append(
                {
                    "date": curr_date,
                    "ticker": ticker,
                    "ratio": round(ratio, 4),
                    "type": "forward_split",  # 正向拆分
                    "adjustment_factor": round(ratio, 4),
                }
            )
    return events
