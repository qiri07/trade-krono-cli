"""prediction_eval_backtest — 预测评估回测执行器。

从 prediction_eval.py 拆分，职责单一：
  · PredictionEvaluator._run_backtest 方法独立实现

PredictionEvaluator 主类保留在 prediction_eval.py 中。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from trade_krono_cli.backtest_engine import BacktestResult
    from trade_krono_cli.prediction_eval import EvalRecord

from trade_krono_cli.config import get_settings  # noqa: PLC0415

# ──────────────────────────────────────────────────────────────────────
# 本地缓存读取（避免调用外部数据源接口）
# ──────────────────────────────────────────────────────────────────────


def _get_price_from_cache(ticker: str, date_str: str) -> float | None:
    """从本地 pipeline_cache.db 读取指定日期的收盘价。

    优先读取本地 SQLite 缓存，避免调用外部数据源接口（baostock/akshare 等
    可能因网络或账户限制不可用）。

    Parameters
    ----------
    ticker : str
        股票代码，如 sh.600519
    date_str : str
        日期，格式 YYYY-MM-DD

    Returns
    -------
    float | None
        收盘价，找不到时返回 None
    """
    db_path = Path(get_settings().cache_dir) / "pipeline_cache.db"
    if not db_path.exists():
        return None

    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute(
            "SELECT data FROM kline_cache WHERE ticker = ? AND end >= ? ORDER BY end DESC LIMIT 1",
            (ticker, date_str),
        )
        row = cur.fetchone()
        conn.close()
        if row is None:
            return None
        df = pd.read_pickle(pd.io.common.BytesIO(row[0]))
        # 找到最接近 date_str 的收盘价（数据库中列名为 timestamps）
        df = df.sort_values("timestamps")
        mask = df["timestamps"] <= pd.Timestamp(date_str)
        if not mask.any():
            return None
        return float(df.loc[mask, "close"].iloc[-1])
    except Exception:
        return None


def run_backtest(
    records: list[EvalRecord],
    rebal_mode: str = "fixed_horizon",
    fixed_horizon: int = 5,
) -> BacktestResult:
    """运行回测引擎，基于 EvalRecord 重建交易日序列。

    简化版：使用 EvalRecord 中的 entry/exit 价格直接模拟，
    不实时获取 K 线（性能优先），约束通过 is_blocked 字段判断。
    """
    from trade_krono_cli.backtest_benchmarks import build_backtest_records
    from trade_krono_cli.backtest_engine import BacktestEngine, BacktestResult  # noqa: PLC0415

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

    # 将 EvalRecord 的价格信息注入到 BacktestRecord（从本地缓存读取，不调用外部接口）
    record_price_map: dict[tuple[str, str], tuple[float, float]] = {}
    for r in records:
        key = (r.ticker, r.eval_date)
        entry = _get_price_from_cache(r.ticker, r.eval_date)
        eval_date_h = (
            datetime.strptime(r.eval_date, "%Y-%m-%d") + timedelta(days=r.horizon_days)
        ).strftime("%Y-%m-%d")
        exit_price = _get_price_from_cache(r.ticker, eval_date_h)
        if entry and exit_price:
            record_price_map[key] = (entry, exit_price)

    for bt_r in bt_records:
        key = (bt_r.ticker, bt_r.date)
        prices = record_price_map.get(key)
        if prices:
            bt_r.entry_price = prices[0]
            bt_r.exit_price = prices[1]

    return engine.run(bt_records)
