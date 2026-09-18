"""prediction_eval_backtest — 预测评估回测执行器。

从 prediction_eval.py 拆分，职责单一：
  · PredictionEvaluator._run_backtest 方法独立实现

PredictionEvaluator 主类保留在 prediction_eval.py 中。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from trade_krono_cli.backtest_engine import BacktestResult  # noqa: PLC0415

if TYPE_CHECKING:
    from trade_krono_cli.prediction_eval import EvalRecord


def run_backtest(
    records: list["EvalRecord"],
    rebal_mode: str = "fixed_horizon",
    fixed_horizon: int = 5,
) -> "BacktestResult":
    """运行回测引擎，基于 EvalRecord 重建交易日序列。

    简化版：使用 EvalRecord 中的 entry/exit 价格直接模拟，
    不实时获取 K 线（性能优先），约束通过 is_blocked 字段判断。
    """
    from trade_krono_cli.backtest_engine import BacktestEngine, BacktestResult  # noqa: PLC0415
    from trade_krono_cli.prediction_eval import _get_close_price, build_backtest_records

    # 选择主要 horizon（优先 5 日）
    primary_horizon = fixed_horizon
    bt_records = build_backtest_records(records, horizon=primary_horizon)
    if not bt_records:
        # fallback: 用最小 horizon
        horizons_sorted = sorted({r.horizon_days for r in records})
        if horizons_sorted:
            bt_records = build_backtest_records(records, horizon=horizons_sorted[0])
    if not bt_records:
        return BacktestResult.empty()

    engine = BacktestEngine(
        rebal_mode=rebal_mode,
        fixed_horizon=primary_horizon,
    )

    # 将 EvalRecord 的价格信息注入到 BacktestRecord
    record_price_map: dict[tuple[str, str], tuple[float, float]] = {}
    for r in records:
        key = (r.ticker, r.eval_date)
        entry = _get_close_price(r.ticker, r.eval_date)
        eval_date_h = (
            datetime.strptime(r.eval_date, "%Y-%m-%d") + timedelta(days=r.horizon_days)
        ).strftime("%Y-%m-%d")
        exit_price = _get_close_price(r.ticker, eval_date_h)
        if entry and exit_price:
            record_price_map[key] = (entry, exit_price)

    for bt_r in bt_records:
        key = (bt_r.ticker, bt_r.date)
        prices = record_price_map.get(key)
        if prices:
            bt_r.entry_price = prices[0]
            bt_r.exit_price = prices[1]

    return engine.run(bt_records)
