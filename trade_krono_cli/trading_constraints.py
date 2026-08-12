"""
A 股交易约束引擎。

提供：
  - 涨跌停价格检测（主板 ±10%，创业板/科创板 ±20%）
  - T+1 买入锁定检查
  - ST/*ST 标的识别
  - 交易成本计算
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

from loguru import logger

from trade_krono_cli.constraints_config import ConstraintConfig
from trade_krono_cli.security import validate_ticker


# ═══════════════════════════════════════════════════════
# 约束结果
# ═══════════════════════════════════════════════════════

@dataclass(frozen=True)
class TradingConstraintResult:
    """单只股票交易约束检查的返回结果。"""

    symbol: str
    allowed: bool
    reason: Optional[str] = None          # 拒绝原因（None 表示通过）
    cost_bps: float = 0.0               # 本次交易所需成本（bps）
    position_locked_until: Optional[date] = None  # T+1 锁定到期日
    limit_up_price: Optional[float] = None    # 今日涨停价
    limit_down_price: Optional[float] = None  # 今日跌停价
    is_st: bool = False                   # 是否为 ST 标的


# ═══════════════════════════════════════════════════════
# ST 检测
# ═══════════════════════════════════════════════════════

# baostock ST 股票名称中常见的标记（实际需查询属性字段）
_ST_PATTERNS = re.compile(r"^(ST|\*ST|SST|N ST)", re.IGNORECASE)


def _is_st_by_name(ticker: str, name_hint: Optional[str] = None) -> bool:
    """
    通过股票代码后缀或名称线索判断 ST。

    baostock 的 ST 标记通常在 name 字段中，这里先按 ticker 后三位做启发式
    判断（实际应在 fetch 数据后检查 name 字段）。
    """
    if name_hint:
        return bool(_ST_PATTERNS.search(name_hint))
    # 无法仅凭 ticker 判断，返回 False（后续通过 query 确认）
    return False


def check_st_status(
    ticker: str,
    config: Optional[ConstraintConfig] = None,
) -> bool:
    """
    检查是否为 ST/*ST 标的。

    注意：当前实现为启发式（基于代码规则）。baostock 可通过
    `bs.query_stock_basic()` 获取 ST 状态，但需要网络连接。
    生产环境中建议在 fetch_lookback 后批量查询一次并缓存。

    Parameters
    ----------
    ticker : 股票代码（如 sh.600519）
    config : 约束配置（unused，预留扩展）

    Returns
    -------
    True 表示是 ST 标的，应被过滤
    """
    if config is None or not config.enable_st_filter:
        return False

    # TODO: 接入 baostock query_stock_basic 获取准确 ST 状态
    # 当前返回 False（不过滤），避免误伤正常股票
    logger.debug(f"ST 检测跳过（启发式未启用）: {ticker}")
    return False


# ═══════════════════════════════════════════════════════
# 涨跌停检测
# ═══════════════════════════════════════════════════════

def detect_exchange(ticker: str) -> str:
    """
    从 ticker 识别交易所前缀。

    Returns
    -------
    "sse" (上交所) | "szse" (深交所) | "unknown"
    """
    ticker = validate_ticker(ticker)
    if ticker.startswith("sh."):
        return "sse"
    elif ticker.startswith("sz."):
        return "szse"
    return "unknown"


def compute_limit_prices(
    prev_close: float,
    ticker: Optional[str] = None,
    config: Optional[ConstraintConfig] = None,
) -> tuple[Optional[float], Optional[float]]:
    """
    根据前一日收盘价计算今日涨跌停价。

    Parameters
    ----------
    prev_close : 前一日收盘价
    ticker : 股票代码（用于判断涨跌停幅度）
    config : 约束配置

    Returns
    -------
    (limit_up_price, limit_down_price)
      任意一个为 None 表示未启用检测
    """
    if config is None or not config.enable_limit_check:
        return None, None

    if prev_close <= 0:
        return None, None

    limit_pct = config.sse_limit_pct  # 默认主板
    if ticker:
        exchange = detect_exchange(ticker)
        code = ticker.split(".")[-1]
        # 科创板(688)在上证，创业板(300/301)在深证，均用20%
        if code.startswith("688") or code.startswith("300") or code.startswith("301"):
            limit_pct = config.szse_limit_pct

    limit_up = round(prev_close * (1 + limit_pct / 100.0), 2)
    limit_down = round(prev_close * (1 - limit_pct / 100.0), 2)
    return limit_up, limit_down


def check_limit_status(
    ticker: str,
    current_price: float,
    prev_close: float,
    kline_df=None,
    config: Optional[ConstraintConfig] = None,
) -> TradingConstraintResult:
    """
    检查当前价格是否触及涨跌停。

    Parameters
    ----------
    ticker : 股票代码
    current_price : 当前价格（当日最高/最低或现价）
    prev_close : 前一日收盘价
    kline_df : K 线 DataFrame（可选，用于历史涨停检测）
    config : 约束配置

    Returns
    -------
    TradingConstraintResult
      - allowed=False + reason="LIMIT_UP"/"LIMIT_DOWN" 表示触及涨跌停
      - limit_up_price / limit_down_price 记录边界值
    """
    if config is None:
        config = ConstraintConfig()

    limit_up, limit_down = compute_limit_prices(prev_close, ticker, config)

    if limit_up is None:
        return TradingConstraintResult(
            symbol=ticker, allowed=True
        )

    # 检查是否触及涨停（current_price >= 涨停价）
    if current_price >= limit_up * 0.999:  # 允许 0.1% 浮点误差
        return TradingConstraintResult(
            symbol=ticker,
            allowed=False,
            reason="LIMIT_UP",
            limit_up_price=limit_up,
            limit_down_price=limit_down,
        )

    # 检查是否触及跌停
    if current_price <= limit_down * 1.001:
        return TradingConstraintResult(
            symbol=ticker,
            allowed=False,
            reason="LIMIT_DOWN",
            limit_up_price=limit_up,
            limit_down_price=limit_down,
        )

    return TradingConstraintResult(
        symbol=ticker,
        allowed=True,
        limit_up_price=limit_up,
        limit_down_price=limit_down,
    )


# ═══════════════════════════════════════════════════════
# T+1 约束
# ═══════════════════════════════════════════════════════

class T1Tracker:
    """
    跟踪当日买入记录，支持 T+1 结算约束检查。

    线程安全：内部使用 dict，建议每次 pipeline run 创建新实例。
    """

    def __init__(self):
        # ticker -> buy_date (str "YYYY-MM-DD")
        self._buys: dict[str, str] = {}

    def record_buy(self, ticker: str, buy_date: str) -> None:
        """记录一笔买入。"""
        self._buys[ticker] = buy_date

    def can_sell(self, ticker: str, sell_date: str) -> bool:
        """
        检查是否可以在 sell_date 卖出 ticker。

        T+1 规则：买入当日不能卖出，次日及之后可以。
        """
        buy_date = self._buys.get(ticker)
        if buy_date is None:
            return True  # 无买入记录，可以自由卖出
        # 简单日期比较：sell_date > buy_date
        return sell_date > buy_date

    def locked_until(self, ticker: str) -> Optional[date]:
        """返回 ticker 被锁定的最早解锁日期。"""
        buy_date = self._buys.get(ticker)
        if buy_date is None:
            return None
        try:
            bd = datetime.strptime(buy_date, "%Y-%m-%d").date()
            return bd + timedelta(days=1)
        except ValueError:
            return None

    def clear(self) -> None:
        """清空所有买入记录（新交易日开始时调用）。"""
        self._buys.clear()


def enforce_t1(
    ticker: str,
    eval_date: str,
    tracker: T1Tracker,
    config: Optional[ConstraintConfig] = None,
) -> TradingConstraintResult:
    """
    对单只股票执行 T+1 约束检查。

    Parameters
    ----------
    ticker : 股票代码
    eval_date : 评估日期（YYYY-MM-DD）
    tracker : T1Tracker 实例
    config : 约束配置

    Returns
    -------
    TradingConstraintResult
    """
    if config is None:
        config = ConstraintConfig()

    if not config.enable_t1:
        return TradingConstraintResult(symbol=ticker, allowed=True)

    if not tracker.can_sell(ticker, eval_date):
        locked_until = tracker.locked_until(ticker)
        return TradingConstraintResult(
            symbol=ticker,
            allowed=False,
            reason=f"T1_LOCKED(until={locked_until})",
            position_locked_until=locked_until,
        )

    return TradingConstraintResult(symbol=ticker, allowed=True)


# ═══════════════════════════════════════════════════════
# 成本模型
# ═══════════════════════════════════════════════════════

def compute_transaction_cost(
    gross_return_pct: float,
    side: str = "sell",
    config: Optional[ConstraintConfig] = None,
) -> float:
    """
    计算交易成本对收益的影响。

    Parameters
    ----------
    gross_return_pct : 毛收益率（%）
    side : "buy" | "sell" | "roundtrip"
    config : 约束配置

    Returns
    -------
    净收益率（%）
    """
    if config is None:
        config = ConstraintConfig()

    if side == "buy":
        return config.apply_cost(gross_return_pct)
    elif side == "sell":
        # 卖出时扣除卖出成本
        if not config.enable_cost_model:
            return gross_return_pct
        return gross_return_pct - config.sell_cost_bps() / 100.0
    elif side == "roundtrip":
        return config.apply_roundtrip_cost(gross_return_pct)
    else:
        return gross_return_pct


# ═══════════════════════════════════════════════════════
# 综合约束检查
# ═══════════════════════════════════════════════════════

def check_all_constraints(
    ticker: str,
    eval_date: str,
    current_price: Optional[float] = None,
    prev_close: Optional[float] = None,
    kline_df=None,
    t1_tracker: Optional[T1Tracker] = None,
    config: Optional[ConstraintConfig] = None,
) -> TradingConstraintResult:
    """
    对单只股票执行全部交易约束检查。

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
        limit_result = check_limit_status(
            ticker, current_price, prev_close, kline_df, config
        )
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
    t1_tracker: Optional[T1Tracker] = None,
    config: Optional[ConstraintConfig] = None,
) -> tuple[list[dict], list[dict]]:
    """
    对合并后的结果列表应用交易约束过滤。

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
            logger.debug(
                f"🚫 {ticker} 被约束拦截: {result.reason}"
            )

    return allowed, rejected
