"""trade_krono_cli.backtest_metrics — 回测绩效指标计算。

职责（从 backtest_engine.py 提取）：
  · 日收益率、总收益率、年化收益率
  · 波动率、夏普比率、最大回撤、卡玛比率
  · 胜率、盈亏比、盈亏因子
  · Maker/Taker 手续费统计
  • 偏度、峰度、最佳/最差单日收益
  • 分期收益分析
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from loguru import logger

TRADING_DAYS_PER_YEAR = 252
RISK_FREE_ANNUAL = 0.025  # 无风险利率 ~2.5%/年


def compute_metrics(
    equity_curve: list[tuple[str, float]],
    trades: list[dict],
    records: list[Any],
    enable_period_analysis: bool = True,
) -> dict:
    """计算完整的回测绩效指标。

    Parameters
    ----------
    equity_curve : list[tuple[str, float]]
        [(日期, 净值), ...] 权益曲线。
    trades : list[dict]
        交易记录列表。
    records : list[Any]
        回测记录列表（用于兼容原接口）。
    enable_period_analysis : bool
        是否启用分期收益分析。

    Returns
    -------
    dict
        绩效指标字典。
    """
    if not equity_curve:
        return {}

    values = np.array([v for _, v in equity_curve], dtype=float)
    n_days = len(values)
    if n_days < 2:
        return {}

    # ── 日收益率序列 ────────────────────────────────────────────────────
    daily_returns = np.diff(values) / values[:-1]

    # ── 总收益率 / 年化收益率 ──────────────────────────────────────────
    total_return = (values[-1] / values[0] - 1) * 100
    years = n_days / TRADING_DAYS_PER_YEAR
    annual_return = (
        ((values[-1] / values[0]) ** (1 / max(years, 0.01)) - 1) * 100
        if years > 0
        else 0.0
    )

    # ── 波动率 / 夏普比率 ──────────────────────────────────────────────
    vol = (
        float(np.std(daily_returns)) * np.sqrt(TRADING_DAYS_PER_YEAR) * 100
        if len(daily_returns) > 1
        else 0.0
    )
    risk_free_daily = RISK_FREE_ANNUAL / TRADING_DAYS_PER_YEAR
    excess_ret = daily_returns - risk_free_daily
    sharpe = (
        float(np.mean(excess_ret) / np.std(excess_ret) * np.sqrt(TRADING_DAYS_PER_YEAR))
        if len(excess_ret) > 1 and np.std(excess_ret) > 1e-12
        else 0.0
    )

    # ── 最大回撤 ────────────────────────────────────────────────────────
    running_max = np.maximum.accumulate(values)
    drawdown = (values - running_max) / running_max * 100
    max_drawdown = float(np.min(drawdown)) if len(drawdown) > 0 else 0.0

    # ── 卡玛比率 ────────────────────────────────────────────────────────
    calmar = annual_return / abs(max_drawdown) if max_drawdown != 0 else 0.0

    # ── 胜率 / 盈亏比 ───────────────────────────────────────────────────
    pnl_list = [t.get("pnl", 0.0) for t in trades if t.get("action") == "SELL"]
    wins = [p for p in pnl_list if p > 0]
    losses = [p for p in pnl_list if p <= 0]
    win_rate = len(wins) / len(pnl_list) * 100 if pnl_list else 0.0
    avg_win = float(np.mean(wins)) if wins else 0.0
    avg_loss = abs(float(np.mean(losses))) if losses else 1e-9
    profit_factor = abs(sum(wins) / sum(losses)) if losses else (100.0 if wins else 0.0)

    # ── Maker/Taker 统计 ───────────────────────────────────────────────
    maker_trades = [t for t in trades if t.get("order_type") == "limit"]
    taker_trades = [t for t in trades if t.get("order_type") == "market"]
    maker_cost_bps = (
        sum(t.get("cost_bps", 0) for t in maker_trades) / max(len(maker_trades), 1)
    )
    taker_cost_bps = (
        sum(t.get("cost_bps", 0) for t in taker_trades) / max(len(taker_trades), 1)
    )

    # ── 收益分布（手动计算偏度/峰度，兼容 numpy 2.0）─────────────────────
    def _skewness(arr: np.ndarray) -> float:
        if len(arr) < 3:
            return 0.0
        m = float(np.mean(arr))
        s = float(np.std(arr, ddof=1))
        if s == 0:
            return 0.0
        return float(np.mean(((arr - m) / s) ** 3))

    def _kurtosis(arr: np.ndarray) -> float:
        if len(arr) < 4:
            return 0.0
        m = float(np.mean(arr))
        s = float(np.std(arr, ddof=1))
        if s == 0:
            return 0.0
        return float(np.mean(((arr - m) / s) ** 4)) - 3.0

    skew = _skewness(daily_returns)
    kurt = _kurtosis(daily_returns)
    # daily_returns 已是小数形式，乘 100 转为百分比
    best_day = float(np.max(daily_returns) * 100) if len(daily_returns) > 0 else 0.0
    worst_day = float(np.min(daily_returns) * 100) if len(daily_returns) > 0 else 0.0

    # ── 分期收益分析 ──────────────────────────────────────────────────
    period_analysis: dict[str, dict] = {}
    if enable_period_analysis and len(equity_curve) >= 2:
        period_analysis = analyze_periods(equity_curve)

    return {
        "total_return_pct": round(total_return, 2),
        "annualized_return_pct": round(annual_return, 2),
        "volatility_annual_pct": round(vol, 2),
        "sharpe_ratio": round(sharpe, 3),
        "max_drawdown_pct": round(max_drawdown, 2),
        "calmar_ratio": round(calmar, 3),
        "win_rate_pct": round(win_rate, 1),
        "profit_factor": round(profit_factor, 3),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "n_wins": len(wins),
        "n_losses": len(losses),
        "skewness": round(skew, 3),
        "kurtosis": round(kurt, 3),
        "best_day_pct": round(best_day, 2),
        "worst_day_pct": round(worst_day, 2),
        "n_trades": len(trades),
        "n_days": n_days,
        # ── Maker/Taker 统计 ────────────────────────────────────────────
        "n_maker_trades": len(maker_trades),
        "n_taker_trades": len(taker_trades),
        "avg_maker_cost_bps": round(maker_cost_bps, 2),
        "avg_taker_cost_bps": round(taker_cost_bps, 2),
        # ── 分期分析 ──────────────────────────────────────────────────
        "period_analysis": period_analysis,
    }


def analyze_periods(
    daily_equity: list[tuple[str, float]],
) -> dict[str, dict]:
    """按时间段分析收益表现。

    Parameters
    ----------
    daily_equity : list[tuple[str, float]]
        [(日期, 净值), ...] 列表。

    Returns
    -------
    dict[str, dict]
        {时期标签: {return_pct, days, start_date, end_date}} 字典。
    """
    if len(daily_equity) < 2:
        return {}

    df = pd.DataFrame(daily_equity, columns=["date", "value"])
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    values = df["value"]

    periods: dict[str, dict] = {}
    start_date = values.index[0]
    end_date = values.index[-1]

    # 定义分析区间（沿用 configs 中的常见区间）
    period_ranges: list[tuple[str, str, str]] = [
        ("Y1", "2025-01-01", "2025-12-31"),
        ("Y2", "2026-01-01", "2026-12-31"),
        ("H1_2026", "2026-01-01", "2026-06-30"),
        ("H2_2026", "2026-07-01", "2026-12-31"),
    ]

    for label, start_str, end_str in period_ranges:
        try:
            start = pd.Timestamp(start_str)
            end = pd.Timestamp(end_str)
        except Exception as exc:
            logger.warning("解析期间日期失败 %s-%s: %s", start_str, end_str, exc)
            continue

        # 检查区间是否与回测区间重叠
        if start > end_date or end < start_date:
            continue

        mask = (values.index >= start) & (values.index <= end)
        subset = values[mask]
        if len(subset) < 2:
            continue

        ret = (subset.iloc[-1] / subset.iloc[0] - 1) * 100
        periods[label] = {
            "return_pct": round(ret, 2),
            "days": len(subset),
            "start_date": str(subset.index[0].date()),
            "end_date": str(subset.index[-1].date()),
        }

    return periods
