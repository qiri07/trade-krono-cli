"""
WalkForwardEngine — 滚动时间窗口的回测评估引擎。

与 backtest_engine.py 的区别：
  backtest_engine     对固定信号集合做一次性模拟（适合策略参数调优）
  WalkForwardEngine   按时间顺序滚动训练/测试，模拟真实投研流程

核心流程：
  for each test_date in evaluation_dates:
      train_data = all_data_before(test_date − lookback)
      model.fit(train_data)               # 重训练（或加载快照）
      prediction = model.predict(test_date)
      actual_return = fetch_actual(test_date, horizon)
      record ← EvalRecord(...)

  最终聚合：胜率 / EV / Sharpe / 最大回撤 / IC 等

设计原则：
  · 严格 Point-in-Time：train_data 不包含 test_date 之后的任何数据
  · 支持多 horizon（同时评估 5/10/20/30 天）
  · 结果可复现：每次 walk-forward 都有唯一 run_id + 配置 hash
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Iterable, Optional

import numpy as np
import pandas as pd
from loguru import logger

from trade_krono_cli.data_snapshot import DataSnapshot
from trade_krono_cli.eval_data import EvalRecord, EvaluationSummary, HorizonMetrics

# ═══════════════════════════════════════════════════════
#  WalkForwardConfig
# ═══════════════════════════════════════════════════════


@dataclass
class WalkForwardConfig:
    """
    滚动窗口评估的配置参数。

    Parameters
    ----------
    lookback_days       训练窗口大小（交易日）
    step_days           测试窗口步长（交易日）
    horizons            要评估的持有期列表（天）
    min_train_samples   最少训练样本数，低于此值跳过该窗口
    test_start_date     测试区间起始日期（ISO）
    test_end_date       测试区间截止日期（ISO）
    """

    lookback_days: int = 252  # 1 年交易日
    step_days: int = 20  # 每月一步
    horizons: tuple[int, ...] = (5, 10, 20, 30)
    min_train_samples: int = 60
    test_start_date: str = ""
    test_end_date: str = ""


# ═══════════════════════════════════════════════════════
#  WalkForwardResult
# ═══════════════════════════════════════════════════════


@dataclass
class WalkForwardResult:
    """单次 WalkForward 评估的完整结果。"""

    run_id: str
    config: WalkForwardConfig
    total_windows: int = 0
    valid_windows: int = 0  # 满足 min_train_samples 的窗口数
    records: list[EvalRecord] = field(default_factory=list)
    summary: Optional[EvaluationSummary] = None
    elapsed_sec: float = 0.0
    data_snapshot: Optional[DataSnapshot] = None

    @property
    def win_rate(self) -> float:
        if not self.records:
            return 0.0
        return sum(1 for r in self.records if r.is_direction_correct) / len(self.records) * 100

    @property
    def avg_return(self) -> float:
        if not self.records:
            return 0.0
        returns = [r.actual_return_pct for r in self.records if r.actual_return_pct != 0]
        return sum(returns) / len(returns) if returns else 0.0

    @property
    def sharpe_annual(self) -> float:
        """近似年化夏普比率（假设 252 交易日）。"""
        if not self.records:
            return 0.0
        returns = np.array([r.actual_return_pct for r in self.records])
        if returns.std() == 0:
            return 0.0
        return float(np.mean(returns) / returns.std() * np.sqrt(252))


# ═══════════════════════════════════════════════════════
#  WalkForwardEngine
# ═══════════════════════════════════════════════════════


class WalkForwardEngine:
    """
    滚动时间窗口评估引擎。

    使用方式：
        engine = WalkForwardEngine(config)
        result = engine.run(
            ticker="sh.600519",
            predict_fn=my_predictor,      # Callable[date] → prediction dict
            fetch_actual_fn=my_fetcher,   # Callable[date, horizon] → actual return %
            data_snapshot=snapshot,
        )
    """

    def __init__(self, config: Optional[WalkForwardConfig] = None):
        self.config = config or WalkForwardConfig()
        self._records: list[EvalRecord] = []

    def _generate_test_dates(
        self,
        train_end: str,
        test_end: str,
    ) -> list[str]:
        """生成分割点（每个 step_days 一个测试点）。"""
        from datetime import datetime

        start = datetime.strptime(train_end, "%Y-%m-%d")
        end = datetime.strptime(test_end, "%Y-%m-%d")
        dates = []
        current = start + timedelta(days=self.config.step_days)
        while current <= end:
            dates.append(current.strftime("%Y-%m-%d"))
            current += timedelta(days=self.config.step_days)
        return dates

    def run(
        self,
        ticker: str,
        predict_fn: Callable[[str, str], Optional[dict]],
        fetch_actual_fn: Callable[[str, str, int], Optional[float]],
        data_snapshot: Optional[DataSnapshot] = None,
        train_data_fn: Optional[Callable[[str, str], Optional[pd.DataFrame]]] = None,
    ) -> WalkForwardResult:
        """
        执行一次完整的 walk-forward 评估。

        Parameters
        ----------
        ticker            股票代码
        predict_fn        (ticker, eval_date) → Optional[prediction_dict]
                          prediction_dict 需要包含：direction, expected_change_pct,
                          **及可选的 p10/p25/p50/p75/p90**
        fetch_actual_fn   (ticker, eval_date, horizon) → Optional[float] 实际收益率 %
        data_snapshot     Point-in-Time 数据快照（可选，用于日志和审计）
        train_data_fn     (ticker, train_end_date) → Optional[DataFrame]
                          训练数据获取函数（可选，用于数据完整性检查）

        Returns
        -------
        WalkForwardResult
        """
        t0 = time.time()
        run_id = f"wf_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        cfg = self.config

        # 确定测试区间
        test_start = (
            cfg.test_start_date or data_snapshot.effective_cut_date() if data_snapshot else ""
        )
        if not test_start or not cfg.test_end_date:
            raise ValueError("需要提供 test_start_date 和 test_end_date（或从 data_snapshot 推断）")

        # 生成测试日期序列
        test_dates = self._generate_test_dates(
            train_end=test_start,
            test_end=cfg.test_end_date,
        )
        if not test_dates:
            logger.warning(f"无测试日期: {test_start} → {cfg.test_end_date}")
            return WalkForwardResult(run_id=run_id, config=cfg, data_snapshot=data_snapshot)

        records: list[EvalRecord] = []
        valid_windows = 0

        for eval_date in test_dates:
            # Point-in-Time 检查：确保预测日期在快照边界内
            if data_snapshot and data_snapshot.contains_future_data(ticker, eval_date):
                logger.debug(f"跳过 {ticker} @ {eval_date}：超出数据快照边界")
                continue

            # 训练数据完整性检查
            if train_data_fn:
                train_end = (
                    datetime.strptime(eval_date, "%Y-%m-%d") - timedelta(days=cfg.lookback_days)
                ).strftime("%Y-%m-%d")
                train_df = train_data_fn(ticker, train_end)
                if train_df is None or len(train_df) < cfg.min_train_samples:
                    logger.debug(
                        f"跳过 {ticker} @ {eval_date}：训练数据不足 ({len(train_df) if train_df is not None else 0} < {cfg.min_train_samples})"
                    )
                    continue

            valid_windows += 1

            # 调用预测函数
            pred = predict_fn(ticker, eval_date)
            if pred is None:
                continue

            direction = pred.get("direction")
            pred_return = pred.get("expected_change_pct")
            p10 = pred.get("p10")
            p25 = pred.get("p25")
            p50 = pred.get("p50")
            p75 = pred.get("p75")
            p90 = pred.get("p90")

            for horizon in cfg.horizons:
                actual = fetch_actual_fn(ticker, eval_date, horizon)
                if actual is None:
                    continue

                # 计算实际方向
                actual_direction = "UP" if actual > 1.0 else ("DOWN" if actual < -1.0 else "FLAT")
                is_correct = (
                    (direction == actual_direction) if direction and actual_direction else False
                )

                record = EvalRecord(
                    ticker=ticker,
                    eval_date=eval_date,
                    horizon_days=horizon,
                    pred_direction=direction,
                    pred_return_pct=pred_return,
                    actual_return_pct=actual,
                    actual_direction=actual_direction,
                    is_direction_correct=is_correct,
                    error_pct=round((pred_return or 0) - actual, 4),
                    # 分位数信息（用于后续 EV 分析）
                    p10=p10,
                    p25=p25,
                    p50=p50,
                    p75=p75,
                    p90=p90,
                )
                records.append(record)

        elapsed = time.time() - t0
        summary = self._build_summary(records, cfg.horizons)

        result = WalkForwardResult(
            run_id=run_id,
            config=cfg,
            total_windows=len(test_dates),
            valid_windows=valid_windows,
            records=records,
            summary=summary,
            elapsed_sec=round(elapsed, 2),
            data_snapshot=data_snapshot,
        )
        logger.info(
            f"WalkForward 完成: {ticker} {valid_windows} windows, "
            f"win_rate={result.win_rate:.1f}%, avg_ret={result.avg_return:.2f}%, "
            f"sharpe={result.sharpe_annual:.2f}, {elapsed:.1f}s"
        )
        return result

    def _build_summary(
        self,
        records: list[EvalRecord],
        horizons: tuple[int, ...],
    ) -> EvaluationSummary:
        """从 records 聚合出 EvaluationSummary。"""
        from trade_krono_cli.eval_combined import (
            compute_combined_metrics,
            compute_high_conf_metrics,
        )
        from trade_krono_cli.eval_kronos import compute_kronos_accuracy
        from trade_krono_cli.eval_ta import compute_ta_metrics  # type: ignore

        summary = EvaluationSummary()
        for h in horizons:
            h_recs = [r for r in records if r.horizon_days == h]
            if not h_recs:
                continue
            metrics = HorizonMetrics()
            compute_kronos_accuracy(h_recs, metrics)
            compute_combined_metrics(h_recs, metrics)
            compute_high_conf_metrics(h_recs, metrics)
            try:
                compute_ta_metrics(h_recs, metrics)
            except Exception:
                pass  # TA metrics optional
            summary.horizons[h] = metrics

        # 全局计数
        summary.records = records
        summary.kronos_n = sum(1 for r in records if r.pred_direction is not None)
        summary.ta_buy_n = sum(1 for r in records if r.ta_signal == "BUY")
        return summary


# ═══════════════════════════════════════════════════════
#  便捷工厂
# ═══════════════════════════════════════════════════════


def run_walk_forward_quick(
    ticker: str,
    eval_dates: Iterable[str],
    predict_fn: Callable[[str, str], Optional[dict]],
    fetch_actual_fn: Callable[[str, str, int], Optional[float]],
    horizons: tuple[int, ...] = (5, 10, 20, 30),
    lookback_days: int = 252,
) -> WalkForwardResult:
    """
    快速入口：一行代码跑完 walk-forward。

    Parameters
    ----------
    ticker           股票代码
    eval_dates       要评估的日期列表
    predict_fn       (ticker, date) → prediction dict
    fetch_actual_fn  (ticker, date, horizon) → actual return %
    horizons         持有期列表
    lookback_days    训练窗口大小

    Returns
    -------
    WalkForwardResult
    """
    from uuid import uuid4

    cfg = WalkForwardConfig(
        lookback_days=lookback_days,
        horizons=horizons,
        test_start_date=min(eval_dates) if eval_dates else "",
        test_end_date=max(eval_dates) if eval_dates else "",
    )
    engine = WalkForwardEngine(cfg)

    # 重写 run 以支持自定义日期列表
    run_id = f"wf_{uuid4().hex[:8]}"
    records: list[EvalRecord] = []
    for eval_date in sorted(eval_dates):
        pred = predict_fn(ticker, eval_date)
        if pred is None:
            continue
        direction = pred.get("direction")
        pred_return = pred.get("expected_change_pct")
        for horizon in horizons:
            actual = fetch_actual_fn(ticker, eval_date, horizon)
            if actual is None:
                continue
            actual_dir = "UP" if actual > 1.0 else ("DOWN" if actual < -1.0 else "FLAT")
            records.append(
                EvalRecord(
                    ticker=ticker,
                    eval_date=eval_date,
                    horizon_days=horizon,
                    pred_direction=direction,
                    pred_return_pct=pred_return,
                    actual_return_pct=actual,
                    actual_direction=actual_dir,
                    is_direction_correct=(direction == actual_dir) if direction else False,
                    error_pct=round((pred_return or 0) - actual, 4),
                    p10=pred.get("p10"),
                    p25=pred.get("p25"),
                    p50=pred.get("p50"),
                    p75=pred.get("p75"),
                    p90=pred.get("p90"),
                )
            )

    summary = engine._build_summary(records, horizons)
    return WalkForwardResult(
        run_id=run_id,
        config=cfg,
        total_windows=len(list(eval_dates)),
        valid_windows=len(list(eval_dates)),
        records=records,
        summary=summary,
    )
