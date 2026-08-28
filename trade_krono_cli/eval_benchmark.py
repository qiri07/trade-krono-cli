"""
Benchmark 与 Alpha 评估模块。

职责：
  · 获取真实指数数据（CSI300 / CSI500 / CSI1000）作为基准
  · 计算策略 vs 基准的超额收益（Alpha）
  · 计算 portfolio 完整绩效指标：CAGR / Volatility / Sharpe / Sortino / Calmar / ...
  · 计算 Turnover（换手率）

基准股票代码（baostock）：
  · 沪深300：sh.000300
  · 中证500：sh.000905
  · 中证1000：sh.000852
  · 上证综指：sh.000001
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from loguru import logger

from trade_krono_cli.data import fetch_kline
from trade_krono_cli.eval_data import EvalRecord

# ── 基准代码映射 ──────────────────────────────────────────────────────────────

BENCHMARK_TICKERS: dict[str, str] = {
    "CSI300": "sh.000300",
    "CSI500": "sh.000905",
    "CSI1000": "sh.000852",
    "SHCOMP": "sh.000001",  # 上证综指（fallback）
}


@dataclass
class BenchmarkResult:
    """单只基准的评估结果。"""

    name: str
    ticker: str
    cumulative_return_pct: float = 0.0
    annualized_return_pct: float = 0.0
    volatility_annual_pct: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    n_days: int = 0
    equity_curve: list[tuple[str, float]] = field(default_factory=list)


@dataclass
class AlphaResult:
    """策略 vs 基准的 Alpha 对比。"""

    strategy_return_pct: float = 0.0
    benchmark_return_pct: float = 0.0
    alpha_pct: float = 0.0  # strategy - benchmark
    benchmark_name: str = ""


# ═══════════════════════════════════════════════════════
# 基准数据获取
# ═══════════════════════════════════════════════════════


def fetch_benchmark_kline(
    ticker: str,
    start_date: str,
    end_date: str,
) -> Optional[list[tuple[str, float]]]:
    """
    拉取基准指数的 K 线数据。

    Returns
    -------
    list[(date_str, close_price)] 或 None
    """
    try:
        df = fetch_kline(ticker, start_date, end_date, frequency="d", use_cache=True)
        if df is None or df.empty:
            return None
        result = []
        for _, row in df.iterrows():
            ts = str(row.get("timestamps", ""))
            close = row.get("close")
            if ts and close is not None:
                try:
                    result.append((ts[:10], float(close)))
                except (ValueError, TypeError):
                    continue
        return result
    except Exception as e:
        logger.debug(f"基准 {ticker} 数据获取失败: {str(e)[:100]}")
        return None


def compute_benchmark_metrics(
    ticker: str,
    name: str,
    start_date: str,
    end_date: str,
) -> Optional[BenchmarkResult]:
    """
    获取基准指数并计算完整绩效指标。
    """
    kline = fetch_benchmark_kline(ticker, start_date, end_date)
    if not kline or len(kline) < 2:
        return None

    dates = [d for d, _ in kline]
    prices = np.array([p for _, p in kline], dtype=float)
    n_days = len(prices)

    if n_days < 2 or prices[0] <= 0:
        return None

    # ── 累计收益 ────────────────────────────────────────────────────────
    cum_return = (prices[-1] / prices[0] - 1) * 100

    # ── 日收益率序列 ────────────────────────────────────────────────────
    daily_ret = np.diff(prices) / prices[:-1]
    trading_days_per_year = 252
    years = n_days / trading_days_per_year

    # ── 年化收益（CAGR）─────────────────────────────────────────────────
    ann_return = (
        ((prices[-1] / prices[0]) ** (1 / max(years, 0.01)) - 1) * 100 if years > 0 else 0.0
    )

    # ── 年化波动率 ─────────────────────────────────────────────────────
    vol = (
        float(np.std(daily_ret, ddof=1)) * np.sqrt(trading_days_per_year) * 100
        if len(daily_ret) > 1 and np.std(daily_ret, ddof=1) > 0
        else 0.0
    )

    # ── 夏普比率 ────────────────────────────────────────────────────────
    rf_daily = 0.025 / trading_days_per_year
    excess = daily_ret - rf_daily
    sharpe = (
        float(np.mean(excess) / np.std(excess) * np.sqrt(trading_days_per_year))
        if len(excess) > 1 and np.std(excess) > 1e-12
        else 0.0
    )

    # ── 最大回撤 ────────────────────────────────────────────────────────
    running_max = np.maximum.accumulate(prices)
    dd = (prices - running_max) / running_max * 100
    max_dd = float(np.min(dd)) if len(dd) > 0 else 0.0

    # ── 权益曲线（百分比累计收益）───────────────────────────────────────
    equity_curve = [(d, round((p / prices[0] - 1) * 100, 4)) for d, p in zip(dates, prices)]

    return BenchmarkResult(
        name=name,
        ticker=ticker,
        cumulative_return_pct=round(cum_return, 2),
        annualized_return_pct=round(ann_return, 2),
        volatility_annual_pct=round(vol, 2),
        sharpe_ratio=round(sharpe, 3),
        max_drawdown_pct=round(max_dd, 2),
        n_days=n_days,
        equity_curve=equity_curve,
    )


# ═══════════════════════════════════════════════════════
# Portfolio Metrics（完整绩效指标）
# ═══════════════════════════════════════════════════════


def compute_portfolio_metrics(
    equity_curve: list[tuple[str, float]],
    trades: list[dict],
) -> dict:
    """
    计算完整的组合绩效指标。

    Returns
    -------
    dict with: cagr, annual_return, volatility, sharpe, sortino,
               max_drawdown, calmar, win_rate, profit_factor, turnover
    """
    if not equity_curve or len(equity_curve) < 2:
        return {}

    values = np.array([v for _, v in equity_curve], dtype=float)
    n_days = len(values)
    trading_days_per_year = 252
    years = n_days / trading_days_per_year

    # ── 日收益率 ────────────────────────────────────────────────────────
    daily_returns = np.diff(values) / values[:-1]

    # ── CAGR / 年化收益 ─────────────────────────────────────────────────
    total_return = (values[-1] / values[0] - 1) * 100
    cagr = ((values[-1] / values[0]) ** (1 / max(years, 0.01)) - 1) * 100 if years > 0 else 0.0

    # ── 年化波动率 ──────────────────────────────────────────────────────
    vol = (
        float(np.std(daily_returns, ddof=1)) * np.sqrt(trading_days_per_year) * 100
        if len(daily_returns) > 1 and np.std(daily_returns, ddof=1) > 1e-12
        else 0.0
    )

    # ── 夏普比率 ────────────────────────────────────────────────────────
    rf_daily = 0.025 / trading_days_per_year
    excess = daily_returns - rf_daily
    sharpe = (
        float(np.mean(excess) / np.std(excess) * np.sqrt(trading_days_per_year))
        if len(excess) > 1 and np.std(excess) > 1e-12
        else 0.0
    )

    # ── Sortino 比率（只惩罚下行波动）───────────────────────────────────
    downside_returns = daily_returns[daily_returns < 0]
    if len(downside_returns) > 1 and np.std(downside_returns, ddof=1) > 1e-12:
        downside_vol = float(np.std(downside_returns, ddof=1)) * np.sqrt(trading_days_per_year)
        sortino = float(np.mean(excess) / downside_vol * np.sqrt(trading_days_per_year))
    else:
        sortino = 0.0

    # ── 最大回撤 / Calmar ───────────────────────────────────────────────
    running_max = np.maximum.accumulate(values)
    drawdown = (values - running_max) / running_max * 100
    max_dd = float(np.min(drawdown)) if len(drawdown) > 0 else 0.0
    calmar = cagr / abs(max_dd) if max_dd != 0 else 0.0

    # ── 胜率 / 盈亏比 ───────────────────────────────────────────────────
    pnl_list = [t.get("pnl", 0.0) for t in trades if t.get("action") == "SELL"]
    wins = [p for p in pnl_list if p > 0]
    losses = [p for p in pnl_list if p <= 0]
    win_rate = len(wins) / len(pnl_list) * 100 if pnl_list else 0.0
    avg_win = float(np.mean(wins)) if wins else 0.0
    avg_loss = abs(float(np.mean(losses))) if losses else 1e-9
    profit_factor = (
        abs(sum(wins) / sum(losses)) if losses and sum(losses) != 0 else (100.0 if wins else 0.0)
    )

    # ── 换手率（Turnover）───────────────────────────────────────────────
    # 买卖次数 / 总交易日数
    nbuys = sum(1 for t in trades if t.get("action") == "BUY")
    nsells = sum(1 for t in trades if t.get("action") == "SELL")
    turnover = (nbuys + nsells) / max(n_days, 1)

    # ── 收益分布 ────────────────────────────────────────────────────────
    def _skew(arr):
        if len(arr) < 3:
            return 0.0
        m, s = np.mean(arr), np.std(arr, ddof=1)
        return float(np.mean(((arr - m) / s) ** 3)) if s > 1e-12 else 0.0

    def _kurt(arr):
        if len(arr) < 4:
            return 0.0
        m, s = np.mean(arr), np.std(arr, ddof=1)
        return float(np.mean(((arr - m) / s) ** 4)) - 3.0 if s > 1e-12 else 0.0

    return {
        "cagr_pct": round(cagr, 2),
        "annual_return_pct": round(cagr, 2),
        "total_return_pct": round(total_return, 2),
        "volatility_annual_pct": round(vol, 2),
        "sharpe_ratio": round(sharpe, 3),
        "sortino_ratio": round(sortino, 3),
        "max_drawdown_pct": round(max_dd, 2),
        "calmar_ratio": round(calmar, 3),
        "win_rate_pct": round(win_rate, 1),
        "profit_factor": round(profit_factor, 3),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "turnover": round(turnover, 2),
        "n_trades": nbuys + nsells,
        "n_days": n_days,
        "skewness": round(_skew(daily_returns), 3),
        "kurtosis": round(_kurt(daily_returns), 3),
        "best_day_pct": round(float(np.max(daily_returns) * 100), 2) if len(daily_returns) else 0.0,
        "worst_day_pct": round(float(np.min(daily_returns) * 100), 2)
        if len(daily_returns)
        else 0.0,
    }


# ═══════════════════════════════════════════════════════
# Alpha 计算：策略 vs 多基准对比
# ═══════════════════════════════════════════════════════


def compute_alpha(
    strategy_return_pct: float,
    records: list[EvalRecord],
    date_range: tuple[str, str],
) -> dict[str, AlphaResult]:
    """
    计算策略相对于各基准的 Alpha。

    Parameters
    ----------
    strategy_return_pct : float
        策略累计收益率（%）
    records : list[EvalRecord]
        回测记录（用于确定日期范围）
    date_range : tuple[str, str]
        (start_date, end_date)

    Returns
    -------
    dict[benchmark_name, AlphaResult]
    """
    start_date, end_date = date_range
    if not records:
        return {}

    results: dict[str, AlphaResult] = {}
    for bench_name, ticker in BENCHMARK_TICKERS.items():
        bm = compute_benchmark_metrics(ticker, bench_name, start_date, end_date)
        if bm is None:
            continue
        results[bench_name] = AlphaResult(
            strategy_return_pct=strategy_return_pct,
            benchmark_return_pct=bm.cumulative_return_pct,
            alpha_pct=round(strategy_return_pct - bm.cumulative_return_pct, 2),
            benchmark_name=bench_name,
        )

    return results


def get_best_alpha(results: dict[str, AlphaResult]) -> Optional[AlphaResult]:
    """返回 Alpha 最大的基准对比结果。"""
    if not results:
        return None
    return max(results.values(), key=lambda a: a.alpha_pct)
