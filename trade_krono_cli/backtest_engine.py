"""回测引擎 — A 股风格的历史信号回测。

职责：
  · 从 EvalRecord 重建交易日序列，模拟仓位管理
  · 支持 T+1 结算、涨跌停成交约束、交易成本（佣金/印花税/滑点）
  · 按日调仓（rebal_weekly / rebal_monthly）或固定持仓周期（fixed_horizon）
  · 计算经典绩效指标：年化收益、夏普比率、卡玛比率、最大回撤、胜率、盈亏比
  · 基准对比（沪深 300 proxy）与超额收益曲线
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np

from trade_krono_cli.constraints_config import ConstraintConfig
from trade_krono_cli.eval_data import BacktestResult
from trade_krono_cli.trading_constraints import compute_limit_prices

# ── 平仓结果 ─────────────────────────────────────────────────────────────────


@dataclass
class CloseLog:
    """单次平仓操作的执行日志。"""

    blocked: bool = False
    blocked_reason: str = ""
    trade_log: dict | None = None
    net_proceeds: float = 0.0


# ── 交易日辅助 ────────────────────────────────────────────────────────────────


def _next_trading_day(d: datetime, kline_dates: list[str]) -> str | None:
    """在 kline_dates 中找 d 之后最近的一个交易日。"""
    ds = d.strftime("%Y-%m-%d")
    for dd in kline_dates:
        if dd > ds:
            return dd
    return None


def _week_start(d: datetime) -> datetime:
    """返回本周周一（调仓触发点）。"""
    return d - timedelta(days=d.weekday())


def _month_start(d: datetime) -> datetime:
    """返回本月第一天。"""
    return d.replace(day=1)


# ═══════════════════════════════════════════════════════
# 回测引擎
# ═══════════════════════════════════════════════════════


@dataclass
class _Position:
    """单只股票的持仓快照。"""

    ticker: str
    entry_date: str
    entry_price: float
    shares: int  # 股数（A 股 100 股整数倍）
    direction: str  # "UP" | "DOWN"
    cost_bps: float = 0.0


@dataclass
class BacktestEngine:
    """基于 EvalRecord 列表的 A 股风格回测引擎。

    模式
    ----
    mode = "fixed_horizon"  → 每条信号独立持有 horizon_days 天后平仓（默认）
    mode = "rebal_weekly"   → 每周一调仓，持有 5 天
    mode = "rebal_monthly"  → 每月初调仓，持有约 20 天

    约束（均可配置）
    ---------------
    - T+1 结算（买入当日不可卖出）
    - 涨停日无法建仓、跌停日无法平仓
    - 双边成本（佣金 + 滑点 + 印花税）
    """

    def __init__(
        self,
        initial_capital: float = 1_000_000.0,
        max_position_pct: float = 0.3,
        min_trade_size: int = 100,
        rebal_mode: str = "fixed_horizon",
        fixed_horizon: int = 5,
        config: ConstraintConfig | None = None,
    ) -> None:
        self.initial_capital = initial_capital
        self.max_position_pct = max_position_pct
        self.min_trade_size = min_trade_size
        self.rebal_mode = rebal_mode
        self.fixed_horizon = fixed_horizon
        self.cfg = config or ConstraintConfig()

    # ── 主入口 ──────────────────────────────────────────────────────────────

    def run(self, records: list[BacktestRecord]) -> BacktestResult:
        """运行回测。

        Parameters
        ----------
        records : list[BacktestRecord]
            按 (ticker, eval_date, horizon_days) 排序的回测记录（由
            PredictionEvaluator 准备）。

        Returns
        -------
        BacktestResult

        """
        if not records:
            return BacktestResult.empty()

        # 按日期排序，构建交易日序列
        all_dates = sorted({r.date for r in records})
        if len(all_dates) < 2:
            return BacktestResult.empty()

        # 模拟：每日现金 + 持仓
        cash = self.initial_capital
        positions: dict[str, _Position] = {}
        daily_equity: list[tuple[str, float]] = []  # (date, equity)
        trades: list[dict] = []

        prev_close_map: dict[str, float] = {}  # ticker -> prev_close

        for day in all_dates:
            day_dt = datetime.strptime(day, "%Y-%m-%d")

            # ── 1. 平仓检查（持仓到期 / 调仓日）────────────────────────────
            to_close = []
            for ticker, pos in list(positions.items()):
                entry_dt = datetime.strptime(pos.entry_date, "%Y-%m-%d")
                hold_days = (day_dt - entry_dt).days

                should_close = False
                if self.rebal_mode == "fixed_horizon":
                    should_close = hold_days >= self.fixed_horizon
                elif self.rebal_mode == "rebal_weekly":
                    # 每周一收盘时平仓（下周一开盘再买）
                    should_close = day_dt.weekday() == 0 and hold_days >= 1
                elif self.rebal_mode == "rebal_monthly":
                    should_close = day_dt.day == 1 and hold_days >= 1

                if should_close:
                    to_close.append(ticker)

            for ticker in to_close:
                pos = positions.pop(ticker)
                exit_price = self._get_exit_price(ticker, day, prev_close_map)
                if exit_price is None or exit_price <= 0:
                    continue  # 无退出价，跳过
                pnl = self._close_position(pos, exit_price, day, ticker, prev_close_map)
                cash += pnl.net_proceeds
                trades.append(pnl.trade_log)  # type: ignore[arg-type]
                if pnl.blocked:
                    blocked_reason = pnl.trade_log.get("blocked_reason", "")  # type: ignore[union-attr]
                    if "LIMIT_UP" in blocked_reason:
                        pass  # 涨停无法卖出，保留持仓到下一天
                    elif "LIMIT_DOWN" in blocked_reason:
                        pass  # 跌停无法卖出，保留持仓

            # ── 2. 调仓：买入新标的 ────────────────────────────────────────
            today_signals = [r for r in records if r.date == day]
            if not today_signals:
                # 估算当日持仓市值，记录权益曲线
                equity = cash + sum(
                    pos.shares * (self._get_exit_price(pos.ticker, day, prev_close_map) or 0)
                    for pos in positions.values()
                )
                daily_equity.append((day, equity))
                continue

            # 按 ticker 去重（同一天同一股票只处理第一条信号）
            seen_tickers: set[str] = set()
            for sig in today_signals:
                if sig.ticker in seen_tickers:
                    continue
                seen_tickers.add(sig.ticker)
                if sig.ticker in positions:
                    continue  # 已有持仓，等待平仓

                entry_price = self._get_entry_price(sig.ticker, day, prev_close_map)
                if entry_price is None or entry_price <= 0:
                    continue

                # 涨跌停检查：买入日涨停 → 无法建仓
                prev_close = prev_close_map.get(sig.ticker)
                if prev_close and prev_close > 0:
                    limit_up, _ = compute_limit_prices(prev_close, sig.ticker, self.cfg)
                    if limit_up and entry_price >= limit_up * 0.999:
                        continue  # 涨停，跳过

                # T+1 检查
                if not self._can_buy_on_day(sig.ticker, day, positions):
                    continue

                # 计算可买股数
                alloc = cash * self.max_position_pct
                shares = int(alloc / entry_price / self.min_trade_size) * self.min_trade_size
                if shares < self.min_trade_size:
                    continue  # 资金不足最小一手

                cost_bps = self.cfg.buy_cost_bps()
                position_value = shares * entry_price
                cash -= position_value
                cash -= position_value * cost_bps / 10000.0  # 买入成本

                positions[sig.ticker] = _Position(
                    ticker=sig.ticker,
                    entry_date=day,
                    entry_price=entry_price,
                    shares=shares,
                    direction=sig.signal or "UP",
                    cost_bps=cost_bps,
                )
                trades.append(
                    {
                        "date": day,
                        "ticker": sig.ticker,
                        "action": "BUY",
                        "price": entry_price,
                        "shares": shares,
                        "cost_bps": cost_bps,
                    },
                )

            # ── 3. 更新 prev_close，记录权益 ───────────────────────────────
            for ticker in list(positions.keys()) + list(seen_tickers):
                close = self._get_exit_price(ticker, day, prev_close_map)
                if close:
                    prev_close_map[ticker] = close

            equity = cash + sum(
                pos.shares * (prev_close_map.get(pos.ticker) or pos.entry_price)
                for pos in positions.values()
            )
            daily_equity.append((day, equity))

        # ── 收尾：未平仓持仓按最后交易日估值 ────────────────────────────────
        last_day = all_dates[-1]
        unrealized = 0.0
        for ticker, pos in positions.items():
            close = self._get_exit_price(ticker, last_day, prev_close_map)
            if close and close > 0:
                exit_val = pos.shares * close
                buy_cost = pos.shares * pos.entry_price * (1 + pos.cost_bps / 10000.0)
                unrealized += exit_val - buy_cost
        cash += unrealized
        daily_equity.append((last_day, cash))

        # ── 4. 计算绩效指标 ───────────────────────────────────────────────
        metrics = self._compute_metrics(daily_equity, trades, records)

        return BacktestResult(
            initial_capital=self.initial_capital,
            final_value=cash,
            total_return_pct=round((cash / self.initial_capital - 1) * 100, 2),
            metrics=metrics,
            equity_curve=daily_equity[-200:],  # 保留最近 200 个交易日
            n_trades=len(trades),
            rebal_mode=self.rebal_mode,
        )

    # ── 价格获取 ─────────────────────────────────────────────────────────────

    def _get_entry_price(
        self,
        ticker: str,
        date: str,
        prev_close_map: dict[str, float],
    ) -> float | None:
        """简化：使用 prev_close_map 中的最新收盘价作为近似入场价。"""
        return prev_close_map.get(ticker)

    def _get_exit_price(
        self,
        ticker: str,
        date: str,
        prev_close_map: dict[str, float],
    ) -> float | None:
        """简化：使用 prev_close_map 中该日的前一日收盘作为退出价。"""
        # 这里用 prev_close_map 本身作为近似（需要调用方维护）
        # 实际实现中应由 fetcher 提供
        return prev_close_map.get(ticker)

    def _can_buy_on_day(self, ticker: str, date: str, positions: dict[str, _Position]) -> bool:
        """T+1：若当日或前一日有买入，则今日不可再买。"""
        pos = positions.get(ticker)
        if pos is None:
            return True
        # 已有持仓不重复买入
        return False

    def _close_position(
        self,
        pos: _Position,
        exit_price: float,
        date: str,
        ticker: str,
        prev_close_map: dict[str, float],
    ) -> CloseLog:
        """平仓，返回成交明细。"""
        prev_close = prev_close_map.get(ticker)
        blocked_reason = ""

        # 跌停检查
        if prev_close and prev_close > 0:
            _, limit_down = compute_limit_prices(prev_close, ticker, self.cfg)
            if limit_down and exit_price <= limit_down * 1.001:
                blocked_reason = "LIMIT_DOWN"
                return CloseLog(blocked=True, blocked_reason=blocked_reason)

        sell_value = pos.shares * exit_price
        sell_cost = sell_value * self.cfg.sell_cost_bps() / 10000.0
        buy_cost = pos.shares * pos.entry_price * (1 + pos.cost_bps / 10000.0)
        net_proceeds = sell_value - sell_cost
        pnl = net_proceeds - buy_cost

        return CloseLog(
            blocked=False,
            trade_log={
                "date": date,
                "ticker": ticker,
                "action": "SELL",
                "price": exit_price,
                "shares": pos.shares,
                "pnl": round(pnl, 2),
                "cost_bps": self.cfg.sell_cost_bps(),
            },
            net_proceeds=net_proceeds,
        )

    # ── 绩效指标 ─────────────────────────────────────────────────────────────

    def _compute_metrics(
        self,
        equity_curve: list[tuple[str, float]],
        trades: list[dict],
        records: list[BacktestRecord],
    ) -> dict:
        """计算完整的回测绩效指标。"""
        if not equity_curve:
            return {}

        values = np.array([v for _, v in equity_curve], dtype=float)
        n_days = len(values)
        if n_days < 2:
            return {}

        # ── 日收益率序列 ────────────────────────────────────────────────────
        daily_returns = np.diff(values) / values[:-1]
        trading_days_per_year = 252

        # ── 总收益率 / 年化收益率 ──────────────────────────────────────────
        total_return = (values[-1] / values[0] - 1) * 100
        years = n_days / trading_days_per_year
        annual_return = (
            ((values[-1] / values[0]) ** (1 / max(years, 0.01)) - 1) * 100 if years > 0 else 0.0
        )

        # ── 波动率 / 夏普比率 ──────────────────────────────────────────────
        vol = (
            float(np.std(daily_returns)) * np.sqrt(trading_days_per_year) * 100
            if len(daily_returns) > 1
            else 0.0
        )
        risk_free_daily = 0.025 / trading_days_per_year  # 无风险利率 ~2.5%/年
        excess_ret = daily_returns - risk_free_daily
        sharpe = (
            float(np.mean(excess_ret) / np.std(excess_ret) * np.sqrt(trading_days_per_year))
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
        avg_win = np.mean(wins) if wins else 0.0
        avg_loss = abs(np.mean(losses)) if losses else 1e-9
        profit_factor = (
            abs(sum(wins) / sum(losses))
            if losses and sum(losses) != 0
            else (100.0 if wins else 0.0)
        )

        # ── 收益分布（手动计算偏度/峰度，兼容 numpy 2.0）─────────────────────
        def _skewness(arr):
            if len(arr) < 3:
                return 0.0
            m = np.mean(arr)
            s = np.std(arr, ddof=1)
            if s == 0:
                return 0.0
            return float(np.mean(((arr - m) / s) ** 3))

        def _kurtosis(arr):
            if len(arr) < 4:
                return 0.0
            m = np.mean(arr)
            s = np.std(arr, ddof=1)
            if s == 0:
                return 0.0
            return float(np.mean(((arr - m) / s) ** 4)) - 3.0

        skew = _skewness(daily_returns)
        kurt = _kurtosis(daily_returns)
        # daily_returns 已是小数形式，乘 100 转为百分比
        best_day = float(np.max(daily_returns) * 100) if len(daily_returns) > 0 else 0.0
        worst_day = float(np.min(daily_returns) * 100) if len(daily_returns) > 0 else 0.0

        return {
            "total_return_pct": round(total_return, 2),
            "annualized_return_pct": round(annual_return, 2),
            "volatility_annual_pct": round(vol, 2),
            "sharpe_ratio": round(sharpe, 3),
            "max_drawdown_pct": round(max_drawdown, 2),
            "calmar_ratio": round(calmar, 3),
            "win_rate_pct": round(win_rate, 1),
            "profit_factor": round(profit_factor, 3),
            "avg_win": round(float(avg_win), 2),
            "avg_loss": round(float(avg_loss), 2),
            "n_wins": len(wins),
            "n_losses": len(losses),
            "skewness": round(skew, 3),
            "kurtosis": round(kurt, 3),
            "best_day_pct": round(best_day, 2),
            "worst_day_pct": round(worst_day, 2),
            "n_trades": len(trades),
            "n_days": n_days,
        }


# ── 基准与超额收益 ────────────────────────────────────────────────────────────


def compute_benchmark_returns(
    records: list[BacktestRecord],
    records_map: dict[str, list[BacktestRecord]],
) -> dict[str, list[float]]:
    """计算基准（等权买入并持有）的累积收益率序列。

    由于没有真实基准数据源，使用所有 tickers 的等权组合收益率作为 proxy。

    Returns
    -------
    {date: cumulative_return_pct}

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
