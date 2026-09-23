"""trading_constraints.t1 — T+1 买入锁定检查。"""

from __future__ import annotations

from trade_krono_cli.constraints_config import ConstraintConfig
from trade_krono_cli.t1_tracker import T1Tracker  # noqa: F401 — 向后兼容导出
from trade_krono_cli.trading_constraints.types import TradingConstraintResult


def enforce_t1(
    ticker: str,
    eval_date: str,
    tracker: T1Tracker,
    config: ConstraintConfig | None = None,
) -> TradingConstraintResult:
    """对单只股票执行 T+1 约束检查。

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
