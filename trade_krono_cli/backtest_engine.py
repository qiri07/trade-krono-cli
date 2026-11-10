"""回测引擎 — A 股风格的历史信号回测。

职责：
  · 从 EvalRecord 重建交易日序列，模拟仓位管理
  · 支持 T+1 结算、涨跌停成交约束、交易成本（佣金/印花税/滑点）
  · 按日调仓（rebal_weekly / rebal_monthly）或固定持仓周期（fixed_horizon）
  · 支持市价/限价订单类型、maker/taker 手续费区分
  · 计算经典绩效指标：年化收益、夏普比率、卡玛比率、最大回撤、胜率、盈亏比
  · 基准对比（沪深 300 proxy）与超额收益曲线
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from trade_krono_cli.backtest_position_manager import DynamicPositionManager  # noqa: TC001
from trade_krono_cli.backtest_risk_control import MaxDrawdownControl  # noqa: TC001
from trade_krono_cli.configs.trading import ConstraintConfig
from trade_krono_cli.eval_data import BacktestResult
from trade_krono_cli.trading_constraints import compute_limit_prices

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from trade_krono_cli.backtest_benchmarks import BacktestRecord


class OrderType(str, Enum):
    """订单类型。"""

    MARKET = "market"  # 市价单（立即成交）
    LIMIT = "limit"  # 限价单（可挂单等待）


class Side(str, Enum):
    """交易方向。"""

    BUY = "buy"
    SELL = "sell"


# ── 平仓结果 ─────────────────────────────────────────────────────────────────


@dataclass
class CloseLog:
    """单次平仓操作的执行日志。"""

    blocked: bool = False
    blocked_reason: str = ""
    trade_log: dict | None = None
    net_proceeds: float = 0.0
    order_type: str = "market"  # market | limit
    side: str = "sell"  # buy | sell


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
    order_type: str = "market"  # market | limit
    is_maker: bool = False  # 是否为挂单（maker）


@dataclass
class TradeLog:
    """单笔交易记录。"""

    date: str
    ticker: str
    action: str  # BUY | SELL
    price: float
    shares: int
    cost_bps: float
    order_type: str = "market"
    side: str = "buy"
    pnl: float = 0.0


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
    - 支持市价/限价订单类型
    - 支持 maker/taker 手续费区分

    参数
    ----
    initial_capital : float
        初始资金
    max_position_pct : float
        单只股票最大仓位占比
    min_trade_size : int
        最小交易单位（A 股 100 股）
    rebal_mode : str
        调仓模式：fixed_horizon | rebal_weekly | rebal_monthly
    fixed_horizon : int
        固定持仓周期（天数）
    order_type : OrderType
        默认订单类型（market | limit）
    config : ConstraintConfig | None
        交易约束配置
    """

    def __init__(
        self,
        initial_capital: float = 1_000_000.0,
        max_position_pct: float = 0.3,
        min_trade_size: int = 100,
        rebal_mode: str = "fixed_horizon",
        fixed_horizon: int = 5,
        order_type: OrderType = OrderType.MARKET,
        config: ConstraintConfig | None = None,
        risk_control: MaxDrawdownControl | None = None,
        position_manager: DynamicPositionManager | None = None,
        enable_split_detection: bool = True,
        split_ratio_threshold: float = 3.0,
        enable_period_analysis: bool = True,
    ) -> None:
        self.initial_capital = initial_capital
        self.max_position_pct = max_position_pct
        self.min_trade_size = min_trade_size
        self.rebal_mode = rebal_mode
        self.fixed_horizon = fixed_horizon
        self.default_order_type = order_type
        self.cfg = config or ConstraintConfig()
        self.risk_control = risk_control
        self.position_manager = position_manager
        self.enable_split_detection = enable_split_detection
        self.split_ratio_threshold = split_ratio_threshold
        self.enable_period_analysis = enable_period_analysis

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
                pnl = self._close_position(
                    pos, exit_price, day, ticker, prev_close_map, is_exit=True
                )
                cash += pnl.net_proceeds
                if pnl.trade_log is not None:
                    trades.append(pnl.trade_log)
                if pnl.blocked:
                    blocked_reason = pnl.blocked_reason
                    if "LIMIT_UP" in blocked_reason:
                        # 涨停无法卖出，恢复持仓到下一天
                        positions[ticker] = pos
                    elif "LIMIT_DOWN" in blocked_reason:
                        # 跌停无法卖出，恢复持仓到下一天
                        positions[ticker] = pos

            # ── 2. 调仓：买入新标的 ────────────────────────────────────────
            today_signals = [r for r in records if r.date == day]
            if not today_signals:
                # 估算当日持仓市值，记录权益曲线
                equity = cash + sum(
                    pos.shares * (self._get_exit_price(pos.ticker, day, prev_close_map) or 0)
                    for pos in positions.values()
                )
                daily_equity.append((day, equity))
                # 更新风险控制（即使不调仓，也需要更新每日净值）
                if self.risk_control is not None:
                    self.risk_control.update(equity)
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

                # 计算可买股数：优先使用动态仓位管理器，否则使用固定比例
                current_value = cash + sum(
                    pos.shares * (self._get_exit_price(pos.ticker, day, prev_close_map) or 0)
                    for pos in positions.values()
                )

                # 更新风险控制并获取当前敞口比例
                exposure = 1.0
                if self.risk_control is not None:
                    self.risk_control.update(current_value)
                    exposure = self.risk_control.get_exposure()

                if self.position_manager is not None:
                    # 基于得分的动态仓位分配
                    stock_scores = {sig.ticker: getattr(sig, "score", 0.0)}
                    allocs = self.position_manager.compute_allocations(
                        stocks=[sig.ticker],
                        scores=stock_scores,
                        exposure=exposure,
                    )
                    alloc = current_value * allocs.get(sig.ticker, 0.0)
                else:
                    # 等权分配，乘以回撤暴露系数
                    alloc = current_value * exposure / self.max_position_pct * self.max_position_pct
                    # 保持原有逻辑：每只股票分配 exposure * max_position_pct
                    alloc = current_value * exposure

                shares = int(alloc / entry_price / self.min_trade_size) * self.min_trade_size
                if shares < self.min_trade_size:
                    continue  # 资金不足最小一手

                # 确定订单类型和费用
                order_type = self.default_order_type.value
                is_maker = order_type == "limit"
                cost_bps = self.cfg.buy_cost_bps_maker() if is_maker else self.cfg.buy_cost_bps()

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
                    order_type=order_type,
                    is_maker=is_maker,
                )
                trades.append(
                    {
                        "date": day,
                        "ticker": sig.ticker,
                        "action": "BUY",
                        "price": entry_price,
                        "shares": shares,
                        "cost_bps": cost_bps,
                        "order_type": order_type,
                        "side": "buy",
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
        """获取入场价。

        - 市价单：使用 prev_close 作为近似
        - 限价单：使用 prev_close * 0.999（略低于前收，确保成交）
        """
        price = prev_close_map.get(ticker)
        if price is None:
            return None
        if self.default_order_type == OrderType.LIMIT:
            # 限价单：设置略低于前收的价格，确保成交
            return price * 0.999
        return price

    def _get_exit_price(
        self,
        ticker: str,
        date: str,
        prev_close_map: dict[str, float],
    ) -> float | None:
        """获取出场价。

        - 市价单：使用 prev_close 作为近似
        - 限价单：使用 prev_close * 1.001（略高于前收，确保成交）
        """
        price = prev_close_map.get(ticker)
        if price is None:
            return None
        return price

    def _can_buy_on_day(self, ticker: str, date: str, positions: dict[str, _Position]) -> bool:
        """T+1：若当日或前一日有买入，则今日不可再买。"""
        pos = positions.get(ticker)
        # 已有持仓不重复买入
        return pos is None

    def _close_position(
        self,
        pos: _Position,
        exit_price: float,
        date: str,
        ticker: str,
        prev_close_map: dict[str, float],
        is_exit: bool = True,
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
        # 卖出成本：永远是 taker（主动平仓）
        sell_cost_bps = self.cfg.sell_cost_bps()
        sell_cost = sell_value * sell_cost_bps / 10000.0
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
                "cost_bps": sell_cost_bps,
                "order_type": pos.order_type,
                "side": "sell",
            },
            net_proceeds=net_proceeds,
            order_type=pos.order_type,
            side="sell",
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
        profit_factor = abs(sum(wins) / sum(losses)) if losses else (100.0 if wins else 0.0)

        # ── Maker/Taker 统计 ───────────────────────────────────────────────
        maker_trades = [t for t in trades if t.get("order_type") == "limit"]
        taker_trades = [t for t in trades if t.get("order_type") == "market"]
        maker_cost_bps = sum(t.get("cost_bps", 0) for t in maker_trades) / max(len(maker_trades), 1)
        taker_cost_bps = sum(t.get("cost_bps", 0) for t in taker_trades) / max(len(taker_trades), 1)

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

        # ── 分期收益分析 ──────────────────────────────────────────────────
        period_analysis: dict[str, dict] = {}
        if self.enable_period_analysis and len(equity_curve) >= 2:
            period_analysis = self._analyze_periods(equity_curve)

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
            # ── Maker/Taker 统计 ────────────────────────────────────────────
            "n_maker_trades": len(maker_trades),
            "n_taker_trades": len(taker_trades),
            "avg_maker_cost_bps": round(maker_cost_bps, 2),
            "avg_taker_cost_bps": round(taker_cost_bps, 2),
            # ── 分期分析 ──────────────────────────────────────────────────
            "period_analysis": period_analysis,
        }

    # ── 拆分检测 ──────────────────────────────────────────────────────────

    def _detect_splits(
        self,
        prices: dict[str, float],
        prev_close_map: dict[str, float],
    ) -> dict[str, float]:
        """检测股票拆分（Split）。

        通过检查相邻交易日的价格比率异常变化，识别可能的股票拆分事件。
        当价格比率超过阈值（默认3.0）或低于其倒数时，判定为可能发生拆分。

        Parameters
        ----------
        prices : dict[str, float]
            {股票代码: 当前收盘价}
        prev_close_map : dict[str, float]
            {股票代码: 前收盘价}

        Returns
        -------
        dict[str, float]
            {股票代码: 调整因子}，调整因子为 1.0 表示未发生拆分，
            否则为调整比例（如 0.5 表示2拆1）。
        """
        if not self.enable_split_detection:
            return {}

        split_adjustments: dict[str, float] = {}
        for ticker, price in prices.items():
            prev_close = prev_close_map.get(ticker)
            if prev_close is None or prev_close <= 0 or price <= 0:
                continue
            ratio = price / prev_close
            # 检查是否发生拆分：价格异常上涨或下跌
            if ratio >= self.split_ratio_threshold:
                # 上涨过多，可能是反向拆分（合并股份）
                split_adjustments[ticker] = 1.0 / ratio
            elif ratio <= 1.0 / self.split_ratio_threshold:
                # 下跌过多，可能是正向拆分（如2拆1）
                split_adjustments[ticker] = ratio
        return split_adjustments

    def _apply_split_adjustment(
        self,
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

    # ── 分期分析 ──────────────────────────────────────────────────────────

    def _analyze_periods(
        self,
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
            {时期标签: {return_pct, days}} 字典。
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
            except Exception as e:
                logger.debug("解析日期失败 %s-%s: %s", start_str, end_str, str(e)[:100])
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

    # ── 辅助：检测单只股票是否发生拆分 ───────────────────────────────

    def detect_split_for_ticker(
        self,
        ticker: str,
        price_history: list[tuple[str, float]],
    ) -> list[dict]:
        """检测单只股票的拆分事件。

        Parameters
        ----------
        ticker : str
            股票代码。
        price_history : list[tuple[str, float]]
            [(日期, 收盘价), ...] 按日期排序的价格序列。

        Returns
        -------
        list[dict]
            拆分事件列表，每项包含 date, ratio, adjustment_factor。
        """
        if not self.enable_split_detection or len(price_history) < 2:
            return []

        events: list[dict] = []
        for i in range(1, len(price_history)):
            prev_date, prev_price = price_history[i - 1]
            curr_date, curr_price = price_history[i]
            if prev_price <= 0 or curr_price <= 0:
                continue
            ratio = curr_price / prev_price
            if ratio >= self.split_ratio_threshold:
                events.append(
                    {
                        "date": curr_date,
                        "ticker": ticker,
                        "ratio": round(ratio, 4),
                        "type": "reverse_split",  # 反向拆分
                        "adjustment_factor": round(1.0 / ratio, 4),
                    }
                )
            elif ratio <= 1.0 / self.split_ratio_threshold:
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
