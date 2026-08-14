"""
预测评估数据层 — 价格获取 + 数据类。

职责：
  • 从 baostock 拉取实际收盘价
  • K 线窗口获取（含涨跌停检测所需的 prev_close）
  • 收益计算与交易成本扣减
  • EvalRecord / HorizonMetrics / EvaluationSummary 数据类
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
from loguru import logger

from trade_krono_cli.constraints_config import ConstraintConfig
from trade_krono_cli.security import validate_ticker, validate_date
from trade_krono_cli.trading_constraints import compute_limit_prices

# 向后兼容：测试通过 patch("trade_krono_cli.eval_data.fetch_kline") 注入
from trade_krono_cli.data import fetch_kline as _fetch_kline_ref
fetch_kline = _fetch_kline_ref


def _resolve_fetch_kline(custom=None):
    """动态解析 fetch_kline，确保测试 patch 生效。"""
    if custom is not None:
        return custom
    # 检查模块级是否有被 patch 的覆盖
    import sys
    mod = sys.modules.get("trade_krono_cli.eval_data")
    if mod is not None and hasattr(mod, "fetch_kline") and mod.fetch_kline is not _fetch_kline_ref:
        return mod.fetch_kline
    return _fetch_kline_ref


# ═══════════════════════════════════════════════════════
# 核心：获取实际价格
# ═══════════════════════════════════════════════════════

def get_close_price(ticker: str, date_str: str, _fetch_kline=None) -> Optional[float]:
    """获取指定日期的收盘价（支持精确日期和最近交易日）。"""
    try:
        ticker = validate_ticker(ticker)
        date_str = validate_date(date_str)
        fetcher = _resolve_fetch_kline(_fetch_kline)
        start = (datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=5)).strftime("%Y-%m-%d")
        end = (datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=5)).strftime("%Y-%m-%d")
        df = fetcher(ticker, start, end, frequency="d", use_cache=True)
        if df.empty:
            return None
        df["date_col"] = pd.to_datetime(df["timestamps"]).dt.strftime("%Y-%m-%d")
        target = df[df["date_col"] == date_str]
        if not target.empty:
            return float(target["close"].iloc[0])
        df_sorted = df.sort_values("timestamps", ascending=False)
        return float(df_sorted["close"].iloc[0])
    except Exception as e:
        logger.debug(f"获取收盘价失败 {ticker} @ {date_str}: {str(e)[:200]}")
        return None


def get_kline_window(
    ticker: str, start_date: str, end_date: str,
    _fetch_kline=None,
) -> Optional[pd.DataFrame]:
    """拉取指定区间的 K 线，失败时返回 None。

    Parameters
    ----------
    _fetch_kline : callable, optional
        注入自定义 fetch_kline，供测试使用。
    """
    fetcher = _resolve_fetch_kline(_fetch_kline)
    try:
        return fetcher(ticker, start_date, end_date, frequency="d", use_cache=True)
    except Exception as e:
        logger.debug(f"K 线拉取失败 {ticker} {start_date}~{end_date}: {str(e)[:200]}")
        return None


def calc_return(entry_price: float, exit_price: float) -> float:
    """计算收益率（%）。"""
    if entry_price <= 0 or exit_price <= 0:
        return 0.0
    return (exit_price - entry_price) / entry_price * 100.0


def is_price_at_limit(
    ticker: str, price: float, prev_close: float, direction: str,
) -> bool:
    """判断价格是否触及涨跌停。

    Parameters
    ----------
    ticker : 股票代码
    price  : 当日收盘价
    prev_close : 前一日收盘价
    direction : "up" | "down"

    Returns
    -------
    True 表示触及对应方向的涨停/跌停
    """
    if prev_close is None or prev_close <= 0:
        return False
    limit_up, limit_down = compute_limit_prices(prev_close, ticker)
    if limit_up is None:
        return False
    if direction == "up":
        return price >= limit_up * 0.999
    else:
        return limit_down is not None and price / limit_down <= 1.001


def apply_roundtrip_cost(gross_return_pct: float, cost_bps: float = 17.0) -> float:
    """扣减双边交易成本后的净收益率（%）。"""
    return round(gross_return_pct - cost_bps / 100.0, 4)


# ═══════════════════════════════════════════════════════
# 预测评估结果数据类
# ═══════════════════════════════════════════════════════

@dataclass
class EvalRecord:
    """单次预测的评估记录。"""
    ticker: str
    eval_date: str
    horizon_days: int
    pred_direction: Optional[str]   # UP / DOWN / FLAT
    pred_return_pct: Optional[float]
    actual_return_pct: float
    actual_direction: str           # UP / DOWN / FLAT
    is_direction_correct: bool      # 方向是否预测正确
    error_pct: float                # 预测误差 = 预测 - 实际
    # ── 分布分位数（来自 PredictionDistribution，可选）──────────────────
    p10: Optional[float] = None
    p25: Optional[float] = None
    p50: Optional[float] = None
    p75: Optional[float] = None
    p90: Optional[float] = None
    # 附加上下文（用于分组统计）
    ta_signal: Optional[str] = None
    composite_score: Optional[float] = None
    # ── 交易约束标记（由约束感知评估写入）──────────────────────
    entry_blocked_limit_up: bool = False   # 买入日涨停，实际无法建仓
    exit_blocked_limit_down: bool = False  # 退出日跌停，实际无法平仓
    cost_bps_applied: float = 0.0          # 本次扣减的交易成本（bps）


@dataclass
class HorizonMetrics:
    """指标汇总按单一 horizon（天）分组。"""
    kronos_dir_accuracy: float = 0.0
    ta_buy_win_rate: float = 0.0
    ta_buy_avg_return: float = 0.0
    ta_hold_avg_return: float = 0.0
    combined_buy_up_win_rate: float = 0.0
    combined_buy_up_avg_return: float = 0.0
    high_conf_win_rate: float = 0.0
    high_conf_avg_return: float = 0.0
    # ── 增强指标（回测引擎补充）─────────────────────────────────────────────
    win_rate_pct: float = 0.0           # 综合胜率
    avg_return_pct: float = 0.0         # 平均收益
    profit_factor: float = 0.0          # 盈亏比
    max_drawdown_pct: float = 0.0       # 最大回撤（%）
    sharpe_ratio: float = 0.0           # 夏普比率


@dataclass
class BacktestResult:
    """单次完整回测的结果。"""
    initial_capital: float = 1_000_000.0
    final_value: float = 0.0
    total_return_pct: float = 0.0
    metrics: dict = field(default_factory=dict)
    equity_curve: list[tuple[str, float]] = field(default_factory=list)
    n_trades: int = 0
    rebal_mode: str = "fixed_horizon"
    records: list = field(default_factory=list)  # 回测使用的 BacktestRecord 列表

    @staticmethod
    def empty() -> "BacktestResult":
        return BacktestResult()


@dataclass
class EvaluationSummary:
    """评估汇总统计。"""
    # 聚合计数
    kronos_n: int = 0
    ta_buy_n: int = 0
    ta_hold_n: int = 0
    combined_buy_up_n: int = 0
    high_conf_n: int = 0
    # 约束拦截计数
    entry_limit_up_blocked: int = 0
    exit_limit_down_blocked: int = 0
    cost_applied_n: int = 0
    # 按 horizon 分组的指标
    horizons: dict[int, HorizonMetrics] = field(default_factory=dict)
    records: list[EvalRecord] = field(default_factory=list)
    # ── 回测结果 ────────────────────────────────────────────────────────────
    backtest: Optional[BacktestResult] = None
    # ── 基准对比 ────────────────────────────────────────────────────────────
    benchmark_cum_return_pct: float = 0.0
    excess_return_pct: float = 0.0
    benchmark_curve: dict[str, float] = field(default_factory=dict)
    excess_curve: dict[str, float] = field(default_factory=dict)
