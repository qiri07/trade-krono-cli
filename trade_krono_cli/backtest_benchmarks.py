"""backtest_benchmarks — 回测基准计算与记录构建。

从 backtest_engine.py 拆分出来，专门负责：
  · 等权组合基准收益率计算（compute_benchmark_returns）
  · 策略 vs 基准超额收益曲线（compute_excess_curve）
  · 回测记录数据类（BacktestRecord）及构建函数（build_backtest_records）
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# ── 基准与超额收益 ────────────────────────────────────────────────────────────


def compute_benchmark_returns(
    records: list["BacktestRecord"],
    records_map: dict[str, list["BacktestRecord"]],
) -> dict[str, list[float]]:
    """计算基准（等权买入并持有）的累积收益率序列。

    由于没有真实基准数据源，使用所有 tickers 的等权组合收益率作为 proxy。

    Returns
    -------
    dict[date, cumulative_return_pct]

    """
    if not records:
        return {}

    all_dates = sorted({r.date for r in records})
    tickers = sorted({r.ticker for r in records})
    if not tickers:
        return {}

    # 为每个 ticker 建立 price timeline
    ticker_prices: dict[str, dict[str, float]] = {}
    for r in records:
        ticker_prices.setdefault(r.ticker, {})
        ticker_prices[r.ticker][r.date] = float(r.exit_price or r.entry_price or 0.0)

    # 等权组合每日收益
    cum_ret: list[float] = [0.0]
    for day in all_dates[1:]:
        daily_returns: list[float] = []
        for tk in tickers:
            prices = ticker_prices.get(tk, {})
            p_today = prices.get(day)
            # 找最近的 prior price
            prior_prices = [v for d, v in sorted(prices.items()) if d < day]
            p_prev = prior_prices[-1] if prior_prices else p_today
            if p_today and p_prev and p_prev > 0:
                daily_returns.append((p_today - p_prev) / p_prev)
        if daily_returns:
            port_ret = np.mean(daily_returns)
            cum_ret.append(cum_ret[-1] + port_ret)
        else:
            cum_ret.append(cum_ret[-1])

    return {d: round(float(r) * 100, 4) for d, r in zip(all_dates, cum_ret, strict=False)}  # type: ignore[arg-type]


def compute_excess_curve(
    strategy_curve: list[tuple[str, float]],
    benchmark_curve: dict[str, float],
) -> list[tuple[str, float]]:
    """计算策略 vs 基准的超额收益累计曲线。"""
    bench_map = dict(benchmark_curve.items())
    excess: list[tuple[str, float]] = []
    cum_excess = 0.0
    for day, strategy_val in strategy_curve:
        bench_val = bench_map.get(day, 0.0)
        cum_excess += (strategy_val - bench_val) / 100.0  # 简化近似
        excess.append((day, round(cum_excess * 100, 4)))
    return excess


# ── 回测记录数据结构 ──────────────────────────────────────────────────────────


@dataclass
class BacktestRecord:
    """回测单条信号的数据快照。"""

    ticker: str
    date: str
    signal: str | None  # "BUY" / "HOLD" / "SELL"
    entry_price: float | None
    exit_price: float | None
    horizon_days: int
    pred_direction: str | None
    actual_return_pct: float | None


def build_backtest_records(
    eval_records: list,  # EvalRecord from PredictionEvaluator
    horizon: int = 5,
) -> list[BacktestRecord]:
    """将 EvalRecord 列表转换为 BacktestRecord 列表。

    筛选出指定 horizon 的记录，按 ticker + date 排序，供 BacktestEngine 使用。
    """
    filtered = [r for r in eval_records if r.horizon_days == horizon]
    records = []
    for r in filtered:
        records.append(
            BacktestRecord(
                ticker=r.ticker,
                date=r.eval_date,
                signal=r.ta_signal,
                entry_price=None,  # 由 engine 从价格数据填充
                exit_price=None,
                horizon_days=horizon,
                pred_direction=r.pred_direction,
                actual_return_pct=r.actual_return_pct,
            ),
        )
    return records
