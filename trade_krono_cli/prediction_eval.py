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
from datetime import datetime, timedelta
from typing import Optional, NamedTuple

from loguru import logger

from trade_krono_cli.eval_data import (
    EvalRecord,
    EvaluationSummary,
    HorizonMetrics,
    BacktestResult,
    get_close_price,
    get_kline_window,
    calc_return,
    is_price_at_limit,
    apply_roundtrip_cost,
)
from trade_krono_cli.eval_kronos import compute_kronos_accuracy
from trade_krono_cli.eval_ta import compute_ta_metrics
from trade_krono_cli.eval_combined import compute_combined_metrics, compute_high_conf_metrics
from trade_krono_cli.eval_ic import compute_ic_metrics
from trade_krono_cli.eval_report import (
    store_summary,
    get_latest_evaluation,
    print_report,
)
from trade_krono_cli.constraints_config import ConstraintConfig
from trade_krono_cli.backtest_engine import (
    BacktestEngine,
    BacktestRecord,
    build_backtest_records,
    compute_benchmark_returns,
    compute_excess_curve,
)

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
        backtest: bool = False,
        rebal_mode: str = "fixed_horizon",
        fixed_horizon: int = 5,
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
        backtest : bool
            是否运行回测引擎并计算绩效指标（年化收益、夏普、最大回撤等）
        rebal_mode : str
            调仓模式：fixed_horizon / rebal_weekly / rebal_monthly
        fixed_horizon : int
            固定持仓周期（天数），仅 rebal_mode=fixed_horizon 时生效

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

        from datetime import datetime

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

        # 4b. 回测引擎 + 基准对比
        if backtest and eval_records:
            logger.info("📈 启动回测引擎...")
            bt_result = self._run_backtest(eval_records, rebal_mode, fixed_horizon)
            full_summary.backtest = bt_result

            # 基准对比
            bench_ret = compute_benchmark_returns(bt_result.records, {})
            full_summary.benchmark_curve = bench_ret
            full_summary.benchmark_cum_return_pct = round(
                list(bench_ret.values())[-1] if bench_ret else 0.0, 2
            )
            if bt_result.total_return_pct != 0.0:
                full_summary.excess_return_pct = round(
                    bt_result.total_return_pct - full_summary.benchmark_cum_return_pct, 2
                )
            full_summary.excess_curve = {
                d: round(bt_val - bench_val, 4)
                for d, (bt_val, bench_val) in zip(
                    [d for d, _ in bt_result.equity_curve],
                    [v for _, v in bt_result.equity_curve],
                )
            } if bt_result.equity_curve else {}

            # 将回测增强指标写入 horizon 汇总
            for horizon in self.HORIZONS:
                h_metrics = full_summary.horizons.get(horizon)
                if h_metrics is None:
                    continue
                h_metrics.win_rate_pct = bt_result.metrics.get("win_rate_pct", 0.0)
                h_metrics.max_drawdown_pct = bt_result.metrics.get("max_drawdown_pct", 0.0)
                h_metrics.sharpe_ratio = bt_result.metrics.get("sharpe_ratio", 0.0)
                h_metrics.profit_factor = bt_result.metrics.get("profit_factor", 0.0)

            logger.info(
                f"✅ 回测完成: 总收益 {bt_result.total_return_pct:+.2f}%, "
                f"夏普 {bt_result.metrics.get('sharpe_ratio', 0):.2f}, "
                f"最大回撤 {bt_result.metrics.get('max_drawdown_pct', 0):.1f}%"
            )

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

            # IC 评估（截面 rank IC）
            compute_ic_metrics(h_records, metrics)

            summary.horizons[horizon] = metrics

        # 聚合 IC 到 summary 顶层（取最小 horizon 的结果）
        if 5 in summary.horizons:
            m5 = summary.horizons[5]
            summary.ic_composite_rank_mean = m5.rank_ic_composite_mean
            summary.ic_composite_rank_ir = m5.rank_ic_composite_ir
            summary.ic_kronos_rank_mean = m5.rank_ic_kronos_mean
            summary.ic_ta_rank_mean = m5.rank_ic_ta_mean

        return summary

    def _run_backtest(
        self,
        records: list[EvalRecord],
        rebal_mode: str = "fixed_horizon",
        fixed_horizon: int = 5,
    ) -> BacktestResult:
        """
        运行回测引擎，基于 EvalRecord 重建交易日序列。

        简化版：使用 EvalRecord 中的 entry/exit 价格直接模拟，
        不实时获取 K 线（性能优先），约束通过 is_blocked 字段判断。
        """
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
                datetime.strptime(r.eval_date, "%Y-%m-%d")
                + timedelta(days=r.horizon_days)
            ).strftime("%Y-%m-%d")
            exit = _get_close_price(r.ticker, eval_date_h)
            if entry and exit:
                record_price_map[key] = (entry, exit)

        for bt_r in bt_records:
            key = (bt_r.ticker, bt_r.date)
            prices = record_price_map.get(key)
            if prices:
                bt_r.entry_price = prices[0]
                bt_r.exit_price = prices[1]

        return engine.run(bt_records)

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
    backtest: bool = False,
    rebal_mode: str = "fixed_horizon",
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
        if backtest and hasattr(summary, 'backtest') and summary.backtest:
            _print_latest_backtest(summary)
        return

    # 完整评估
    summary = evaluator.evaluate(
        from_date=from_date,
        to_date=to_date,
        tickers=tickers,
        store=True,
        backtest=backtest,
        rebal_mode=rebal_mode,
    )
    evaluator.print_report(summary)
    if backtest and summary.backtest:
        _print_backtest_report(summary)


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


# ── 回测报告打印 ─────────────────────────────────────────────────────────────

def _print_backtest_report(summary: EvaluationSummary) -> None:
    """打印回测绩效报告。"""
    bt = summary.backtest
    if not bt:
        return
    m = bt.metrics

    print()
    print("╔══════════════════════════════════════════════════════════╗")
    print("║              📈 回测绩效报告（增强版）                    ║")
    print("╠══════════════════════════════════════════════════════════╣")
    print(f"║  模式: {bt.rebal_mode:<44} ║")
    print(f"║  交易次数: {bt.n_trades:<45} ║")
    print(f"║  交易日数: {m.get('n_days', 0):<45} ║")
    print("╠══════════════════════════════════════════════════════════╣")
    print(f"║  总收益率:   {m.get('total_return_pct', 0):>+7.2f}%{'':>30} ║")
    print(f"║  年化收益:   {m.get('annualized_return_pct', 0):>+7.2f}%{'':>30} ║")
    print(f"║  波动率(年): {m.get('volatility_annual_pct', 0):>7.2f}%{'':>30} ║")
    print("╠══════════════════════════════════════════════════════════╣")
    print(f"║  夏普比率:   {m.get('sharpe_ratio', 0):>7.3f}{'':>30} ║")
    print(f"║  卡玛比率:   {m.get('calmar_ratio', 0):>7.3f}{'':>30} ║")
    print(f"║  最大回撤:   {m.get('max_drawdown_pct', 0):>+7.2f}%{'':>30} ║")
    print("╠══════════════════════════════════════════════════════════╣")
    print(f"║  胜率:       {m.get('win_rate_pct', 0):>7.1f}%{'':>30} ║")
    print(f"║  盈亏比:     {m.get('profit_factor', 0):>7.3f}{'':>30} ║")
    print(f"║  平均盈利:   {m.get('avg_win', 0):>+7.2f}%{'':>30} ║")
    print(f"║  平均亏损:   {m.get('avg_loss', 0):>+7.2f}%{'':>30} ║")
    print("╠══════════════════════════════════════════════════════════╣")
    print(f"║  收益偏度:   {m.get('skewness', 0):>7.3f}{'':>30} ║")
    print(f"║  收益峰度:   {m.get('kurtosis', 0):>7.3f}{'':>30} ║")
    print(f"║  最佳日:     {m.get('best_day_pct', 0):>+7.2f}%{'':>30} ║")
    print(f"║  最差日:     {m.get('worst_day_pct', 0):>+7.2f}%{'':>30} ║")
    print("╠══════════════════════════════════════════════════════════╣")
    if summary.benchmark_cum_return_pct != 0.0:
        print(f"║  基准累计收益: {summary.benchmark_cum_return_pct:>+7.2f}%{'':>24} ║")
        print(f"║  超额收益:    {summary.excess_return_pct:>+7.2f}%{'':>24} ║")
    else:
        print(f"║  基准收益: 无数据{'':>40} ║")
    print("╚══════════════════════════════════════════════════════════╝")
    print()


def _print_latest_backtest(summary: dict) -> None:
    """打印最新评估中的回测报告（dict 格式）。"""
    bt = summary.get("backtest")
    if not bt:
        return
    m = bt.get("metrics", {})
    print()
    print("╔══════════════════════════════════════════════════════════╗")
    print("║              📈 回测绩效报告                              ║")
    print("╠══════════════════════════════════════════════════════════╣")
    print(f"║  总收益率:   {m.get('total_return_pct', 0):>+7.2f}%{'':>30} ║")
    print(f"║  年化收益:   {m.get('annualized_return_pct', 0):>+7.2f}%{'':>30} ║")
    print(f"║  夏普比率:   {m.get('sharpe_ratio', 0):>7.3f}{'':>30} ║")
    print(f"║  最大回撤:   {m.get('max_drawdown_pct', 0):>+7.2f}%{'':>30} ║")
    print(f"║  胜率:       {m.get('win_rate_pct', 0):>7.1f}%{'':>30} ║")
    print(f"║  盈亏比:     {m.get('profit_factor', 0):>7.3f}{'':>30} ║")
    print("╚══════════════════════════════════════════════════════════╝")
