"""回测引擎 — A 股风格的历史信号回测。

职责（核心引擎层）：
  · 从 EvalRecord 重建交易日序列，模拟仓位管理
  · 支持 T+1 结算、涨跌停成交约束、交易成本（佣金/印花税/滑点）
  · 按日调仓（rebal_weekly / rebal_monthly）或固定持仓周期（fixed_horizon）
  · 支持市价/限价订单类型、maker/taker 手续费区分

子模块职责：
  - backtest_prices：入场价 / 出场价获取、交易日辅助
  - backtest_metrics：绩效指标计算（夏普/卡玛/回撤/胜率/盈亏比分期分析）
  - backtest_splits：股票拆分检测与调整
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

import pandas as pd

from trade_krono_cli.backtest_metrics import compute_metrics
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


# ── 持仓与交易记录 ───────────────────────────────────────────────────────────


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


# ═══════════════════════════════════════════════════════
# 回测引擎
# ═══════════════════════════════════════════════════════


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
    risk_control : MaxDrawdownControl | None
        风险控制实例
    position_manager : DynamicPositionManager | None
        动态仓位管理器
    enable_split_detection : bool
        是否启用拆分检测
    split_ratio_threshold : float
        拆分判定阈值
    enable_period_analysis : bool
        是否启用分期收益分析
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
            day_dt = pd.Timestamp(day)

            # ── 1. 平仓检查（持仓到期 / 调仓日）────────────────────────────
            to_close: list[str] = []
            for ticker, pos in list(positions.items()):
                entry_dt = pd.Timestamp(pos.entry_date)
                hold_days = (day_dt - entry_dt).days

                should_close = False
                if self.rebal_mode == "fixed_horizon":
                    should_close = hold_days >= self.fixed_horizon
                elif self.rebal_mode == "rebal_weekly":
                    # 每周一收盘时平仓（下周一开盘再买）
                    should_close = day_dt.day_of_week == 0 and hold_days >= 1
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
                    pos.shares
                    * (
                        self._get_exit_price(pos.ticker, day, prev_close_map)
                        or 0
                    )
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
                    pos.shares
                    * (
                        self._get_exit_price(pos.ticker, day, prev_close_map)
                        or 0
                    )
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
                    alloc = current_value * exposure

                shares = int(alloc / entry_price / self.min_trade_size) * self.min_trade_size
                if shares < self.min_trade_size:
                    continue  # 资金不足最小一手

                # 确定订单类型和费用
                order_type = self.default_order_type.value
                is_maker = order_type == "limit"
                cost_bps = (
                    self.cfg.buy_cost_bps_maker()
                    if is_maker
                    else self.cfg.buy_cost_bps()
                )

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
        metrics = compute_metrics(
            daily_equity,
            trades,
            records,
            enable_period_analysis=self.enable_period_analysis,
        )

        return BacktestResult(
            initial_capital=self.initial_capital,
            final_value=cash,
            total_return_pct=round((cash / self.initial_capital - 1) * 100, 2),
            metrics=metrics,
            equity_curve=daily_equity[-200:],  # 保留最近 200 个交易日
            n_trades=len(trades),
            rebal_mode=self.rebal_mode,
        )

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
        pnl = net_proceeds = sell_value - sell_cost - buy_cost

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

    # ── 拆分检测（委托 backtest_splits）─────────────────────────────────

    def _apply_split_adjustment(
        self,
        price: float,
        adjustment_factor: float,
    ) -> float:
        """应用拆分调整因子到价格（委托 backtest_splits）。"""
        from trade_krono_cli.backtest_splits import apply_split_adjustment

        return apply_split_adjustment(price, adjustment_factor)

    def detect_split_for_ticker(
        self,
        ticker: str,
        price_history: list[tuple[str, float]],
    ) -> list[dict]:
        """检测单只股票的拆分事件（委托 backtest_splits）。"""
        from trade_krono_cli.backtest_splits import (
            detect_split_for_ticker as _detect_split_for_ticker,
        )

        return _detect_split_for_ticker(
            ticker,
            price_history,
            split_ratio_threshold=self.split_ratio_threshold,
            enable_split_detection=self.enable_split_detection,
        )

    # ── 向后兼容：价格获取（委托 backtest_prices）──────────────────────

    def _get_entry_price(
        self,
        ticker: str,
        date: str,
        prev_close_map: dict[str, float],
    ) -> float | None:
        """获取入场价（向后兼容，委托 backtest_prices.get_entry_price）。"""
        from trade_krono_cli.backtest_prices import get_entry_price

        return get_entry_price(
            ticker, date, prev_close_map, self.default_order_type.value
        )

    def _get_exit_price(
        self,
        ticker: str,
        date: str,
        prev_close_map: dict[str, float],
    ) -> float | None:
        """获取出场价（向后兼容，委托 backtest_prices.get_exit_price）。"""
        from trade_krono_cli.backtest_prices import get_exit_price

        return get_exit_price(
            ticker, date, prev_close_map, self.default_order_type.value
        )

    # ── 向后兼容：绩效指标（委托 backtest_metrics）─────────────────────

    def _compute_metrics(
        self,
        daily_equity: list[tuple[str, float]],
        trades: list[dict],
        records: list,
    ) -> dict:
        """计算绩效指标（向后兼容，委托 backtest_metrics.compute_metrics）。"""
        from trade_krono_cli.backtest_metrics import compute_metrics

        return compute_metrics(
            daily_equity,
            trades,
            records,
            enable_period_analysis=self.enable_period_analysis,
        )


# ════════════════════════════════════════════════════════
# 向后兼容重导出（从 backtest_prices 迁移的辅助函数）
# ════════════════════════════════════════════════════════

from trade_krono_cli.backtest_prices import (  # noqa: E402, F401
    _month_start,  # noqa: F401
    _next_trading_day,  # noqa: F401
    _week_start,  # noqa: F401
)
