"""prediction_eval_report — 预测评估报告打印工具。

从 prediction_eval.py 拆分出来，专门负责评估结果的展示层。
核心评估逻辑（PredictionEvaluator 类）保留在 prediction_eval.py 中。
"""

from __future__ import annotations

from loguru import logger

from trade_krono_cli.eval_data import EvaluationSummary


def print_latest_kronos(summary: dict) -> None:
    """打印 Kronos 方向准确率报告。"""
    logger.info("┌─ Kronos 方向准确率 ─────────────────────────────────┐")
    logger.info(f"│  样本数: {summary.get('kronos_n', 0)}                              │")
    for h in [5, 10, 20]:
        acc = summary.get("kronos_dir_accuracy", {}).get(str(h), 0)
        marker = "✅" if acc > 55 else "⚠️" if acc > 50 else "❌"
        logger.info(f"│  {marker} {h}D 准确率: {acc:5.1f}%                       │")
    logger.info("└" + "─" * 58 + "┘")
    logger.info("")


def print_latest_ta(summary: dict) -> None:
    """打印 TA BUY 信号表现报告。"""
    logger.info("┌─ TA BUY 信号表现 ───────────────────────────────────┐")
    ta_buy_n = sum(1 for r in summary.get("records", []) if r.ta_signal == "BUY")
    logger.info(f"│  样本数: {ta_buy_n}                             │")
    for h in [5, 10, 20]:
        wr = summary.get("ta_buy_win_rate", {}).get(str(h), 0)
        avg_ret = summary.get("ta_buy_avg_return", {}).get(str(h), 0)
        marker = "✅" if wr > 55 else "⚠️" if wr > 50 else "❌"
        logger.info(
            f"│  {marker} {h}D 胜率: {wr:5.1f}%  平均收益: {avg_ret:+.2f}%                    │",
        )
    logger.info("└" + "─" * 58 + "┘")
    logger.info("")


def print_latest_combined(summary: dict) -> None:
    """打印综合信号（TA BUY + Kronos UP）报告。"""
    logger.info("┌─ 综合信号（TA BUY + Kronos UP）─────────────────────┐")
    combined_n = sum(
        1 for r in summary.get("records", []) if r.ta_signal == "BUY" and r.pred_direction == "UP"
    )
    logger.info(f"│  样本数: {combined_n}                          │")
    for h in [5, 10, 20]:
        wr = summary.get("combined_buy_up_win_rate", {}).get(str(h), 0)
        avg_ret = summary.get("combined_buy_up_avg_return", {}).get(str(h), 0)
        marker = "✅" if wr > 60 else "⚠️" if wr > 55 else "❌"
        logger.info(
            f"│  {marker} {h}D 胜率: {wr:5.1f}%  平均收益: {avg_ret:+.2f}%                    │",
        )
    logger.info("└" + "─" * 58 + "┘")
    logger.info("")


def print_backtest_report(summary: EvaluationSummary) -> None:
    """打印回测绩效报告。"""
    bt = summary.backtest
    if not bt:
        return
    m = bt.metrics

    logger.info("")
    logger.info("╔══════════════════════════════════════════════════════════╗")
    logger.info("║              📈 回测绩效报告（增强版）                    ║")
    logger.info("╠══════════════════════════════════════════════════════════╣")
    logger.info(f"║  模式: {bt.rebal_mode:<44} ║")
    logger.info(f"║  交易次数: {bt.n_trades:<45} ║")
    logger.info(f"║  交易日数: {m.get('n_days', 0):<45} ║")
    logger.info("╠══════════════════════════════════════════════════════════╣")
    logger.info(f"║  总收益率:   {m.get('total_return_pct', 0):>+7.2f}%{'':>30} ║")
    logger.info(f"║  年化收益:   {m.get('annualized_return_pct', 0):>+7.2f}%{'':>30} ║")
    logger.info(f"║  波动率(年): {m.get('volatility_annual_pct', 0):>7.2f}%{'':>30} ║")
    logger.info("╠══════════════════════════════════════════════════════════╣")
    logger.info(f"║  夏普比率:   {m.get('sharpe_ratio', 0):>7.3f}{'':>30} ║")
    logger.info(f"║  卡玛比率:   {m.get('calmar_ratio', 0):>7.3f}{'':>30} ║")
    logger.info(f"║  最大回撤:   {m.get('max_drawdown_pct', 0):>+7.2f}%{'':>30} ║")
    logger.info("╠══════════════════════════════════════════════════════════╣")
    logger.info(f"║  胜率:       {m.get('win_rate_pct', 0):>7.1f}%{'':>30} ║")
    logger.info(f"║  盈亏比:     {m.get('profit_factor', 0):>7.3f}{'':>30} ║")
    logger.info(f"║  平均盈利:   {m.get('avg_win', 0):>+7.2f}%{'':>30} ║")
    logger.info(f"║  平均亏损:   {m.get('avg_loss', 0):>+7.2f}%{'':>30} ║")
    logger.info("╠══════════════════════════════════════════════════════════╣")
    logger.info(f"║  收益偏度:   {m.get('skewness', 0):>7.3f}{'':>30} ║")
    logger.info(f"║  收益峰度:   {m.get('kurtosis', 0):>7.3f}{'':>30} ║")
    logger.info(f"║  最佳日:     {m.get('best_day_pct', 0):>+7.2f}%{'':>30} ║")
    logger.info(f"║  最差日:     {m.get('worst_day_pct', 0):>+7.2f}%{'':>30} ║")
    logger.info("╠══════════════════════════════════════════════════════════╣")
    if summary.benchmark_cum_return_pct != 0.0:
        logger.info(f"║  基准累计收益: {summary.benchmark_cum_return_pct:>+7.2f}%{'':>24} ║")
        logger.info(f"║  超额收益:    {summary.excess_return_pct:>+7.2f}%{'':>24} ║")
    else:
        logger.info(f"║  基准收益: 无数据{'':>40} ║")
    logger.info("╚══════════════════════════════════════════════════════════╝")
    logger.info("")


def print_latest_backtest(summary: dict) -> None:
    """打印最新评估中的回测报告（dict 格式）。"""
    bt = summary.get("backtest")
    if not bt:
        return
    m = bt.get("metrics", {})
    logger.info("")
    logger.info("╔══════════════════════════════════════════════════════════╗")
    logger.info("║              📈 回测绩效报告                              ║")
    logger.info("╠══════════════════════════════════════════════════════════╣")
    logger.info(f"║  总收益率:   {m.get('total_return_pct', 0):>+7.2f}%{'':>30} ║")
    logger.info(f"║  年化收益:   {m.get('annualized_return_pct', 0):>+7.2f}%{'':>30} ║")
    logger.info(f"║  夏普比率:   {m.get('sharpe_ratio', 0):>7.3f}{'':>30} ║")
    logger.info(f"║  最大回撤:   {m.get('max_drawdown_pct', 0):>+7.2f}%{'':>30} ║")
    logger.info(f"║  胜率:       {m.get('win_rate_pct', 0):>7.1f}%{'':>30} ║")
    logger.info(f"║  盈亏比:     {m.get('profit_factor', 0):>7.3f}{'':>30} ║")
    logger.info("╚══════════════════════════════════════════════════════════╝")
