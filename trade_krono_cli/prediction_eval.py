"""
Prediction Evaluation — 预测评估模块入口。

职责：协调各子模块，对外暴露统一的 PredictionEvaluator / run_evaluation 接口。

子模块：
  eval_data.py      — 价格获取、数据类（EvalRecord / EvaluationSummary）
  eval_kronos.py    — Kronos 方向准确率
  eval_ta.py        — TA BUY/HOLD 胜率
  eval_combined.py  — 综合信号 + 高置信度评估
  eval_report.py    — 报告生成与持久化

向后兼容：所有旧名称（_get_close_price、fetch_kline 等）均在此重新导出，
          保证测试中 patch("trade_krono_cli.prediction_eval.*") 正常工作。
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Optional, NamedTuple

from loguru import logger

from trade_krono_cli.eval_data import (
    EvalRecord,
    EvaluationSummary,
    HorizonMetrics,
    get_close_price,
    get_kline_window,
    calc_return,
    is_price_at_limit,
    apply_roundtrip_cost,
)
from trade_krono_cli.eval_kronos import compute_kronos_accuracy
from trade_krono_cli.eval_ta import compute_ta_metrics
from trade_krono_cli.eval_combined import compute_combined_metrics, compute_high_conf_metrics
from trade_krono_cli.eval_report import (
    store_summary,
    get_latest_evaluation,
    print_report,
)
from trade_krono_cli.constraints_config import ConstraintConfig

# ── 向后兼容别名（测试通过 patch("trade_krono_cli.prediction_eval.*") 使用）──
# fetch_kline 是测试直接 patch 的模块级依赖，需在此重新绑定
from trade_krono_cli.data import fetch_kline  # noqa: F401

def _get_close_price(ticker: str, date_str: str, **kwargs) -> Optional[float]:
    """Wrapper: forwards _fetch_kline injection for test compatibility."""
    return get_close_price(ticker, date_str, **kwargs)

_get_kline_window = get_kline_window  # type: ignore[misc]
_calc_return = calc_return            # type: ignore[misc]
_is_price_at_limit = is_price_at_limit  # type: ignore[misc]
_apply_roundtrip_cost = apply_roundtrip_cost  # type: ignore[misc]


# ── 待评估信号数据结构 ────────────────────────────────────────────────────────
class _EvalSignal(NamedTuple):
    """单条待评估信号，字段含义明确，替代匿名 8 元组。"""
    job_id: str
    ticker: str
    eval_date: str
    ta_signal: Optional[str]
    kronos_direction: Optional[str]
    composite_score: Optional[float]
    kronos_change: Optional[float]


# ═══════════════════════════════════════════════════════
# 评估器
# ═══════════════════════════════════════════════════════

class PredictionEvaluator:
    """
    历史预测评估器。

    工作流程：
      1. 从 ResearchDatabase 读取历史 jobs + signals
      2. 对每个信号，获取实际价格并计算 realized return
      3. 按 horizon (5/10/20 天) 分组统计（委托给 eval_* 子模块）
      4. 输出评估报告
    """

    HORIZONS = [5, 10, 20]

    def __init__(self, max_workers: int = 4):
        from trade_krono_cli.research_db import get_research
        self._research = get_research()
        self._max_workers = max_workers

    def evaluate(
        self,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        tickers: Optional[list[str]] = None,
        store: bool = True,
    ) -> EvaluationSummary:
        """
        执行预测评估。

        Parameters
        ----------
        from_date : str, optional
            起始分析日期（YYYY-MM-DD），默认从数据库最早记录开始
        to_date : str, optional
            截止分析日期，默认到今天
        tickers : list[str], optional
            只评估指定股票，默认全部
        store : bool
            是否将评估结果写入 research database

        Returns
        -------
        EvaluationSummary
        """
        logger.info("📊 开始预测评估...")
        t0 = time.time()

        # 1. 获取候选评估数据
        jobs = self._research.list_jobs(limit=1000)
        if from_date:
            jobs = [j for j in jobs if j["date"] >= from_date]
        if to_date:
            jobs = [j for j in jobs if j["date"] <= to_date]
        if not jobs:
            logger.warning("⚠️  没有可评估的历史作业")
            return EvaluationSummary()

        # 2. 收集所有需要评估的信号
        records_to_eval: list[_EvalSignal] = []

        for job in jobs:
            signals = self._research.get_signals_by_job(job["job_id"])
            for sig in signals:
                if sig.get("ta_error") and sig.get("kronos_error"):
                    continue
                if tickers and sig["ticker"] not in tickers:
                    continue
                records_to_eval.append(_EvalSignal(
                    job_id=job["job_id"],
                    ticker=sig["ticker"],
                    eval_date=job["date"],
                    ta_signal=sig.get("ta_signal"),
                    kronos_direction=sig.get("kronos_direction"),
                    composite_score=sig.get("composite_score"),
                    kronos_change=sig.get("kronos_change"),
                ))

        if not records_to_eval:
            logger.warning("⚠️  没有可评估的信号")
            return EvaluationSummary()

        logger.info(f"📋 共 {len(records_to_eval)} 条信号待评估")

        # 3. 逐条获取实际价格并计算 realized return（含交易约束）
        cfg = ConstraintConfig()
        eval_records: list[EvalRecord] = []
        summary = EvaluationSummary()

        from datetime import datetime, timedelta

        for i, sig in enumerate(records_to_eval, 1):
            entry_price = _get_close_price(sig.ticker, sig.eval_date)
            if entry_price is None or entry_price <= 0:
                logger.debug(f"  ⏭️ 跳过 {sig.ticker} @ {sig.eval_date}: 无入口价格")
                continue

            entry_start = (
                datetime.strptime(sig.eval_date, "%Y-%m-%d") - timedelta(days=15)
            ).strftime("%Y-%m-%d")
            entry_kline = _get_kline_window(sig.ticker, entry_start, sig.eval_date)
            entry_prev_close = None
            if entry_kline is not None and len(entry_kline) >= 2:
                entry_prev_close = float(entry_kline["close"].iloc[-2])

            for horizon in self.HORIZONS:
                eval_date_h = (
                    datetime.strptime(sig.eval_date, "%Y-%m-%d")
                    + timedelta(days=horizon)
                ).strftime("%Y-%m-%d")
                exit_price = _get_close_price(sig.ticker, eval_date_h)

                if exit_price is None or exit_price <= 0:
                    continue

                # ── 约束检查 1：买入日是否涨停（无法建仓）──────────────
                entry_blocked = False
                if entry_prev_close and entry_prev_close > 0:
                    entry_blocked = _is_price_at_limit(
                        sig.ticker, entry_price, entry_prev_close, direction="up"
                    )
                if entry_blocked:
                    summary.entry_limit_up_blocked += 1
                    continue

                # ── 约束检查 2：退出日是否跌停（无法平仓）──────────────
                exit_blocked = False
                exit_start = (
                    datetime.strptime(eval_date_h, "%Y-%m-%d") - timedelta(days=5)
                ).strftime("%Y-%m-%d")
                exit_prev_kline = _get_kline_window(sig.ticker, exit_start, eval_date_h)
                if exit_prev_kline is not None and len(exit_prev_kline) >= 2:
                    exit_prev_close = float(exit_prev_kline["close"].iloc[-2])
                    exit_blocked = _is_price_at_limit(
                        sig.ticker, exit_price, exit_prev_close, direction="down"
                    )

                if exit_blocked:
                    summary.exit_limit_down_blocked += 1
                    continue

                # ── 计算净收益（扣减交易成本）─────────────────────────
                gross_return = _calc_return(entry_price, exit_price)
                cost_bps = cfg.total_roundtrip_bps()
                net_return = _apply_roundtrip_cost(gross_return, cost_bps)
                summary.cost_applied_n += 1

                actual_dir = "UP" if net_return > 1.0 else ("DOWN" if net_return < -1.0 else "FLAT")

                pred_dir = str(sig.kronos_direction) if sig.kronos_direction is not None else None
                pred_ret = sig.kronos_change

                is_dir_correct = False
                if pred_dir and pred_dir != "FLAT":
                    is_dir_correct = (pred_dir == actual_dir)

                error = (pred_ret - gross_return) if pred_ret is not None else 0.0

                eval_records.append(EvalRecord(
                    ticker=sig.ticker,
                    eval_date=sig.eval_date,
                    horizon_days=horizon,
                    pred_direction=pred_dir,
                    pred_return_pct=pred_ret,
                    actual_return_pct=round(net_return, 4),
                    actual_direction=actual_dir,
                    is_direction_correct=is_dir_correct,
                    error_pct=round(error, 4),
                    ta_signal=sig.ta_signal,
                    composite_score=float(sig.composite_score) if sig.composite_score is not None else None,
                    entry_blocked_limit_up=False,
                    exit_blocked_limit_down=False,
                    cost_bps_applied=cost_bps,
                ))

            if i % 20 == 0:
                logger.info(f"  进度: {i}/{len(records_to_eval)}")

        elapsed = time.time() - t0
        blocked_str = (
            f"，约束拦截 {summary.entry_limit_up_blocked}涨停买入+{summary.exit_limit_down_blocked}跌停卖出"
            if summary.entry_limit_up_blocked or summary.exit_limit_down_blocked
            else ""
        )
        logger.info(
            f"✅ 评估完成: {len(eval_records)} 条记录, "
            f"扣成本 {summary.cost_applied_n} 条{blocked_str}, 耗时 {elapsed:.1f}s"
        )

        # 4. 计算统计（委托给子模块）
        full_summary = self._compute_summary(eval_records)
        full_summary.entry_limit_up_blocked = summary.entry_limit_up_blocked
        full_summary.exit_limit_down_blocked = summary.exit_limit_down_blocked
        full_summary.cost_applied_n = summary.cost_applied_n

        # 5. 存储到 research DB
        if store:
            self._store_summary(full_summary, jobs[0]["date"] if jobs else None)

        return full_summary

    def _compute_summary(self, records: list[EvalRecord]) -> EvaluationSummary:
        """根据评估记录计算汇总统计（委托给各评估子模块）。"""
        summary = EvaluationSummary(records=records)

        for horizon in self.HORIZONS:
            h_records = [r for r in records if r.horizon_days == horizon]
            if not h_records:
                continue

            metrics = HorizonMetrics()

            summary.kronos_n += compute_kronos_accuracy(h_records, metrics)
            ta_buy_n, ta_hold_n = compute_ta_metrics(h_records, metrics)
            summary.ta_buy_n += ta_buy_n
            summary.ta_hold_n += ta_hold_n
            summary.combined_buy_up_n += compute_combined_metrics(h_records, metrics)
            summary.high_conf_n += compute_high_conf_metrics(h_records, metrics)

            summary.horizons[horizon] = metrics

        return summary

    def _store_summary(
        self, summary: EvaluationSummary, eval_date_range: Optional[str],
    ) -> None:
        """将评估结果存储到 research database。"""
        db_path = self._research._db_path
        store_summary(summary, db_path, eval_date_range)
        logger.info("💾 评估结果已存储到研究数据库")

    def get_latest_evaluation(self) -> Optional[dict]:
        """获取最新的评估结果。"""
        db_path = self._research._db_path
        return get_latest_evaluation(db_path)

    def print_report(self, summary: EvaluationSummary) -> None:
        """打印评估报告到控制台。"""
        print_report(summary, self.HORIZONS)


# ═══════════════════════════════════════════════════════
# CLI 入口
# ═══════════════════════════════════════════════════════

def run_evaluation(
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    tickers: Optional[list[str]] = None,
    latest: bool = False,
) -> None:
    """执行预测评估并打印报告。"""
    evaluator = PredictionEvaluator()

    if latest:
        result = evaluator.get_latest_evaluation()
        if not result:
            print("⚠️  暂无评估结果，请先运行完整评估")
            return
        print()
        print("=" * 60)
        print("  📊 最新评估结果")
        print("=" * 60)
        print(f"  评估时间: {datetime.fromtimestamp(result['eval_at']).strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"  评估日期范围: {result['eval_date_range'] or '全部'}")
        print(f"  评估记录数: {result['n_records']}")
        print()
        summary = result["summary"]

        _print_latest_kronos(summary)
        _print_latest_ta(summary)
        _print_latest_combined(summary)
        return

    # 完整评估
    summary = evaluator.evaluate(
        from_date=from_date,
        to_date=to_date,
        tickers=tickers,
        store=True,
    )
    evaluator.print_report(summary)


def _print_latest_kronos(summary: dict) -> None:
    print("┌─ Kronos 方向准确率 ─────────────────────────────────┐")
    print(f"│  样本数: {summary.get('kronos_n', 0)}                              │")
    for h in [5, 10, 20]:
        acc = summary.get("kronos_dir_accuracy", {}).get(str(h), 0)
        marker = "✅" if acc > 55 else "⚠️" if acc > 50 else "❌"
        print(f"│  {marker} {h}D 准确率: {acc:5.1f}%                       │")
    print("└" + "─" * 58 + "┘")
    print()


def _print_latest_ta(summary: dict) -> None:
    print("┌─ TA BUY 信号表现 ───────────────────────────────────┐")
    ta_buy_n = sum(1 for r in summary.get("records", []) if r.ta_signal == "BUY")
    print(f"│  样本数: {ta_buy_n}                             │")
    for h in [5, 10, 20]:
        wr = summary.get("ta_buy_win_rate", {}).get(str(h), 0)
        avg_ret = summary.get("ta_buy_avg_return", {}).get(str(h), 0)
        marker = "✅" if wr > 55 else "⚠️" if wr > 50 else "❌"
        print(f"│  {marker} {h}D 胜率: {wr:5.1f}%  "
              f"平均收益: {avg_ret:+.2f}%                    │")
    print("└" + "─" * 58 + "┘")
    print()


def _print_latest_combined(summary: dict) -> None:
    print("┌─ 综合信号（TA BUY + Kronos UP）─────────────────────┐")
    combined_n = sum(
        1 for r in summary.get("records", [])
        if r.ta_signal == "BUY" and r.pred_direction == "UP"
    )
    print(f"│  样本数: {combined_n}                          │")
    for h in [5, 10, 20]:
        wr = summary.get("combined_buy_up_win_rate", {}).get(str(h), 0)
        avg_ret = summary.get("combined_buy_up_avg_return", {}).get(str(h), 0)
        marker = "✅" if wr > 60 else "⚠️" if wr > 55 else "❌"
        print(f"│  {marker} {h}D 胜率: {wr:5.1f}%  "
              f"平均收益: {avg_ret:+.2f}%                    │")
    print("└" + "─" * 58 + "┘")
    print()
