"""trading_constraints — A 股交易约束引擎。

提供：
  - 涨跌停价格检测（主板 ±10%，创业板/科创板 ±20%）
  - T+1 买入锁定检查
  - ST/*ST 标的识别
  - 交易成本计算

子模块：
  types        — TradingConstraintResult 数据类
  st_check     — ST 标的识别
  limits       — 涨跌停检测
  t1           — T+1 约束
  cost         — 交易成本计算
"""

from __future__ import annotations

from loguru import logger

from trade_krono_cli.constraints_config import ConstraintConfig
from trade_krono_cli.trading_constraints.cost import compute_transaction_cost
from trade_krono_cli.trading_constraints.limits import (
    check_limit_status,
    compute_limit_prices,
    detect_exchange,
)
from trade_krono_cli.trading_constraints.st_check import _is_st_by_name, check_st_status
from trade_krono_cli.trading_constraints.t1 import T1Tracker, enforce_t1  # noqa: F401
from trade_krono_cli.trading_constraints.types import TradingConstraintResult

__all__ = [
    "TradingConstraintResult",
    "T1Tracker",
    "_is_st_by_name",
    "check_st_status",
    "detect_exchange",
    "compute_limit_prices",
    "check_limit_status",
    "enforce_t1",
    "compute_transaction_cost",
    "check_all_constraints",
    "filter_by_constraints",
]


def check_all_constraints(
    ticker: str,
    eval_date: str,
    current_price: float | None = None,
    prev_close: float | None = None,
    kline_df=None,
    t1_tracker: T1Tracker | None = None,
    config: ConstraintConfig | None = None,
) -> TradingConstraintResult:
    """对单只股票执行全部交易约束检查。

    优先级：
      1. ST 过滤
      2. 涨跌停检测
      3. T+1 锁定

    Parameters
    ----------
    ticker : 股票代码
    eval_date : 评估日期
    current_price : 当前价格（用于涨跌停检测，可选）
    prev_close : 前一日收盘价（用于涨跌停检测，可选）
    kline_df : K 线 DataFrame（可选，用于提取 prev_close）
    t1_tracker : T+1 跟踪器（可选）
    config : 约束配置

    Returns
    -------
    TradingConstraintResult

    """
    if config is None:
        config = ConstraintConfig()

    # 1. ST 检查
    if config.enable_st_filter:
        is_st = check_st_status(ticker, config)
        if is_st:
            return TradingConstraintResult(
                symbol=ticker,
                allowed=False,
                reason="ST_FILTER",
                is_st=True,
            )

    # 2. 涨跌停检查
    if current_price is not None and prev_close is not None:
        limit_result = check_limit_status(ticker, current_price, prev_close, kline_df, config)
        if not limit_result.allowed:
            return limit_result

    # 3. T+1 检查
    if t1_tracker is not None:
        t1_result = enforce_t1(ticker, eval_date, t1_tracker, config)
        if not t1_result.allowed:
            return t1_result

    return TradingConstraintResult(symbol=ticker, allowed=True)


def filter_by_constraints(
    merged_items: list[dict],
    t1_tracker: T1Tracker | None = None,
    config: ConstraintConfig | None = None,
) -> tuple[list[dict], list[dict]]:
    """对合并后的结果列表应用交易约束过滤。

    Parameters
    ----------
    merged_items : merge_results 输出的列表
    t1_tracker : T+1 跟踪器
    config : 约束配置

    Returns
    -------
    (allowed_items, rejected_items)
      allowed_items  — 通过所有约束的结果
      rejected_items — 被约束拦截的结果（标记 reason）

    """
    if config is None:
        config = ConstraintConfig()

    allowed: list[dict] = []
    rejected: list[dict] = []

    for item in merged_items:
        ticker = item.get("ticker", "")
        # 从 merged item 中提取价格信息（如果有）
        last_close = item.get("kronos_last_close")
        pred_close = item.get("kronos_pred_close")

        result = check_all_constraints(
            ticker=ticker,
            eval_date=item.get("date", ""),
            current_price=pred_close,
            prev_close=last_close,
            t1_tracker=t1_tracker,
            config=config,
        )

        if result.allowed:
            allowed.append(item)
        else:
            # 标记被拦截的原因
            item["constraint_reason"] = result.reason
            item["constraint_is_st"] = result.is_st
            item["constraint_limit_up"] = result.limit_up_price
            item["constraint_limit_down"] = result.limit_down_price
            rejected.append(item)
            logger.debug(f"🚫 {ticker} 被约束拦截: {result.reason}")

    return allowed, rejected
