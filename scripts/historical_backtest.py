#!/usr/bin/env python3
"""
历史回测验证脚本 — 对每日流程结果进行历史检查

功能：
1. 从 research DB 读取历史信号
2. 计算各预测周期（5/10/20 天）的方向准确率、胜率、收益
3. 运行回测引擎计算绩效指标（夏普、最大回撤等）
4. 输出详细报告到控制台和文件
5. 支持按来源分组统计（buffett、resonance、multi_head 等）
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger

from trade_krono_cli.backtest_benchmarks import build_backtest_records
from trade_krono_cli.backtest_engine import BacktestEngine, OrderType
from trade_krono_cli.configs.trading import ConstraintConfig
from trade_krono_cli.eval_data import EvalRecord, apply_roundtrip_cost, calc_return, get_close_price
from trade_krono_cli.prediction_eval_backtest import _get_price_from_cache
from trade_krono_cli.research_db import get_research
from trade_krono_cli.trading_constraints import compute_limit_prices

# ─────────────────────────────────────────────────────────────────────────────
# 信号来源分类
# ─────────────────────────────────────────────────────────────────────────────

def classify_signal_source(notes: str, ta_signal: str | None) -> str:
    """根据 job notes 和 TA 信号判断信号来源"""
    if not notes:
        notes = ""
    notes_lower = notes.lower()
    if "buffett" in notes_lower or "巴菲特" in notes_lower:
        return "buffett"
    elif "resonance" in notes_lower or "三线共振" in notes_lower or "macd_resonance" in notes_lower:
        return "resonance"
    elif "multi_head" in notes_lower or "多头排列" in notes_lower:
        return "multi_head"
    elif "double_ma" in notes_lower or "双均线" in notes_lower:
        return "double_ma"
    elif "macd_ma" in notes_lower or "macd+均线" in notes_lower:
        return "macd_ma"
    elif "six_dim" in notes_lower or "六维" in notes_lower:
        return "six_dim"
    elif "combined" in notes_lower or "综合" in notes_lower or "交集" in notes_lower:
        return "combined"
    elif ta_signal and "多头排列" in ta_signal:
        return "multi_head"
    elif ta_signal and "共振" in ta_signal:
        return "resonance"
    return "unknown"


# ─────────────────────────────────────────────────────────────────────────────
# 核心评估逻辑
# ─────────────────────────────────────────────────────────────────────────────

def build_eval_records(
    from_date: str,
    to_date: str,
    tickers: list[str] | None = None,
    horizons: list[int] | None = None,
) -> list[EvalRecord]:
    """从 research DB 构建评估记录"""
    if horizons is None:
        horizons = [5, 10, 20]

    research = get_research()
    jobs = research.list_jobs(limit=2000)

    # 过滤日期
    jobs = [j for j in jobs if from_date <= j["date"] <= to_date]
    if not jobs:
        logger.warning(f"⚠️ 日期范围 {from_date} ~ {to_date} 内无作业")
        return []

    logger.info(f"📋 找到 {len(jobs)} 个作业，日期范围: {from_date} ~ {to_date}")

    eval_records: list[EvalRecord] = []
    cfg = ConstraintConfig()

    for job in jobs:
        job_date = str(job.get("date") or "")
        job_id = str(job.get("job_id") or "")

        signals = research.get_signals_by_job(job_id)
        if not signals:
            continue

        for sig in signals:
            if tickers and sig["ticker"] not in tickers:
                continue
            if sig.get("ta_error") and sig.get("kronos_error"):
                continue

            ticker = sig["ticker"]
            ta_signal = sig.get("ta_signal")
            kronos_dir = sig.get("kronos_direction")
            kronos_change = sig.get("kronos_change")
            composite_score = sig.get("composite_score")

            # 获取入场价
            entry_price = get_close_price(ticker, job_date)
            if entry_price is None or entry_price <= 0:
                logger.debug(f"  ⏭️ 跳过 {ticker} @ {job_date}: 无入口价格")
                continue

            # 获取前一日收盘价（涨跌停检查用）
            entry_start = (datetime.strptime(job_date, "%Y-%m-%d") - timedelta(days=15)).strftime("%Y-%m-%d")
            entry_kline = get_kline_window(ticker, entry_start, job_date)
            entry_prev_close = None
            if entry_kline is not None and len(entry_kline) >= 2:
                entry_prev_close = float(entry_kline["close"].iloc[-2])

            for horizon in horizons:
                eval_date_h = (
                    datetime.strptime(job_date, "%Y-%m-%d") + timedelta(days=horizon)
                ).strftime("%Y-%m-%d")

                # 跳过未来日期（超出缓存范围）
                if eval_date_h > datetime.now().strftime("%Y-%m-%d"):
                    continue

                exit_price = get_close_price(ticker, eval_date_h)

                if exit_price is None or exit_price <= 0:
                    continue

                # ── 约束检查：买入日涨停 ──────────────
                entry_blocked = False
                if entry_prev_close and entry_prev_close > 0:
                    entry_blocked = is_price_at_limit(
                        ticker, entry_price, entry_prev_close, direction="up"
                    )

                # ── 约束检查：退出日跌停 ──────────────
                exit_blocked = False
                exit_start = (
                    datetime.strptime(eval_date_h, "%Y-%m-%d") - timedelta(days=5)
                ).strftime("%Y-%m-%d")
                exit_prev_kline = get_kline_window(ticker, exit_start, eval_date_h)
                if exit_prev_kline is not None and len(exit_prev_kline) >= 2:
                    exit_prev_close = float(exit_prev_kline["close"].iloc[-2])
                    exit_blocked = is_price_at_limit(
                        ticker, exit_price, exit_prev_close, direction="down"
                    )

                if entry_blocked or exit_blocked:
                    continue

                # ── 计算收益 ──────────────
                gross_return = calc_return(entry_price, exit_price)
                cost_bps = cfg.total_roundtrip_bps()
                net_return = apply_roundtrip_cost(gross_return, cost_bps)

                actual_dir = "UP" if net_return > 1.0 else ("DOWN" if net_return < -1.0 else "FLAT")

                pred_dir = str(kronos_dir) if kronos_dir is not None else None
                pred_ret = kronos_change

                is_dir_correct = False
                if pred_dir and pred_dir != "FLAT":
                    is_dir_correct = pred_dir == actual_dir

                error = (pred_ret - gross_return) if pred_ret is not None else 0.0

                eval_records.append(
                    EvalRecord(
                        ticker=ticker,
                        eval_date=job_date,
                        horizon_days=horizon,
                        pred_direction=pred_dir,
                        pred_return_pct=pred_ret,
                        actual_return_pct=round(net_return, 4),
                        actual_direction=actual_dir,
                        is_direction_correct=is_dir_correct,
                        error_pct=round(error, 4),
                        ta_signal=ta_signal,
                        composite_score=float(composite_score) if composite_score is not None else None,
                        entry_blocked_limit_up=entry_blocked,
                        exit_blocked_limit_down=exit_blocked,
                        cost_bps_applied=cost_bps,
                    )
                )

    logger.info(f"✅ 构建完成: {len(eval_records)} 条评估记录")
    return eval_records


def is_price_at_limit(
    ticker: str,
    price: float,
    prev_close: float,
    direction: str,
) -> bool:
    """判断价格是否触及涨跌停"""
    if prev_close is None or prev_close <= 0:
        return False
    limit_up, limit_down = compute_limit_prices(prev_close, ticker)
    if limit_up is None:
        return False
    if direction == "up":
        return price >= limit_up * 0.999
    return limit_down is not None and price / limit_down <= 1.001


def get_kline_window(ticker: str, start_date: str, end_date: str):
    """获取 K 线窗口（使用本地缓存）"""
    from trade_krono_cli.data import fetch_kline
    try:
        return fetch_kline(ticker, start_date, end_date, frequency="d", use_cache=True)
    except Exception as e:
        logger.debug(f"K 线获取失败 {ticker}: {e}")
        return None


def compute_metrics_by_horizon(records: list[EvalRecord], horizons: list[int]) -> dict:
    """按 horizon 计算指标"""
    results: dict[int, dict[str, float]] = {}
    for h in horizons:
        h_records = [r for r in records if r.horizon_days == h]
        if not h_records:
            results[h] = {"count": 0}
            continue

        # 方向准确率（仅有预测方向的）
        has_pred = [r for r in h_records if r.pred_direction and r.pred_direction != "FLAT"]
        dir_acc = sum(1 for r in has_pred if r.is_direction_correct) / len(has_pred) * 100 if has_pred else 0

        # TA 买入胜率
        ta_buy = [r for r in h_records if r.ta_signal and "买" in str(r.ta_signal) or "多头" in str(r.ta_signal) or "共振" in str(r.ta_signal)]
        ta_buy_win = sum(1 for r in ta_buy if r.actual_direction == "UP") / len(ta_buy) * 100 if ta_buy else 0

        # 平均收益
        avg_return = sum(r.actual_return_pct for r in h_records) / len(h_records)
        avg_abs_return = sum(abs(r.actual_return_pct) for r in h_records) / len(h_records)

        # 盈亏比
        wins = [r.actual_return_pct for r in h_records if r.actual_return_pct > 0]
        losses = [r.actual_return_pct for r in h_records if r.actual_return_pct <= 0]
        profit_factor = abs(sum(wins) / sum(losses)) if losses else (100.0 if wins else 0.0)

        results[h] = {
            "count": len(h_records),
            "dir_accuracy_pct": round(dir_acc, 1),
            "ta_buy_win_pct": round(ta_buy_win, 1),
            "avg_return_pct": round(avg_return, 2),
            "avg_abs_return_pct": round(avg_abs_return, 2),
            "profit_factor": round(profit_factor, 2),
            "n_wins": len(wins),
            "n_losses": len(losses),
        }
    return results


def compute_metrics_by_source(records: list[EvalRecord], horizons: list[int]) -> dict:
    """按信号来源计算指标"""
    # 这里需要按 source 分组，但 EvalRecord 没有 source 字段
    # 暂时返回空，后续可通过 job notes 关联
    return {}


def run_backtest_for_records(records: list[EvalRecord], horizon: int = 5) -> dict:
    """运行回测引擎"""
    bt_records = build_backtest_records(records, horizon=horizon)
    if not bt_records:
        return {"error": "No backtest records"}

    # 注入价格
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

    engine = BacktestEngine(
        rebal_mode="fixed_horizon",
        fixed_horizon=horizon,
        order_type=OrderType.MARKET,
        config=ConstraintConfig(),
    )

    result = engine.run(bt_records)
    return {
        "total_return_pct": result.total_return_pct,
        "annualized_return_pct": result.metrics.get("annualized_return_pct", 0),
        "sharpe_ratio": result.metrics.get("sharpe_ratio", 0),
        "max_drawdown_pct": result.metrics.get("max_drawdown_pct", 0),
        "calmar_ratio": result.metrics.get("calmar_ratio", 0),
        "win_rate_pct": result.metrics.get("win_rate_pct", 0),
        "profit_factor": result.metrics.get("profit_factor", 0),
        "n_trades": result.n_trades,
        "n_days": result.metrics.get("n_days", 0),
    }


def print_report(
    eval_records: list[EvalRecord],
    horizon_metrics: dict,
    backtest_results: dict,
    from_date: str,
    to_date: str,
) -> None:
    """打印评估报告"""
    print("\n" + "=" * 80)
    print(f"📊 历史回测验证报告  |  {from_date} ~ {to_date}")
    print("=" * 80)

    # 总体统计
    total_signals = len(eval_records)
    unique_tickers = len({r.ticker for r in eval_records})
    unique_dates = len({r.eval_date for r in eval_records})

    print("\n📈 总体统计:")
    print(f"   评估记录数: {total_signals}")
    print(f"   涉及股票数: {unique_tickers}")
    print(f"   涉及交易日数: {unique_dates}")

    # 按 horizon 汇总
    print("\n🎯 按预测周期分析:")
    print(f"{'周期':>6} | {'记录数':>6} | {'方向准确率':>10} | {'TA买入胜率':>10} | {'平均收益%':>10} | {'盈亏比':>8} | {'胜/负'}")
    print("-" * 80)
    for h in sorted(horizon_metrics.keys()):
        m = horizon_metrics[h]
        if m["count"] == 0:
            print(f"{h:>6} | {'无数据':>6}")
            continue
        print(
            f"{h:>6} | {m['count']:>6} | "
            f"{m['dir_accuracy_pct']:>9.1f}% | "
            f"{m['ta_buy_win_pct']:>9.1f}% | "
            f"{m['avg_return_pct']:>9.2f}% | "
            f"{m['profit_factor']:>7.2f} | "
            f"{m['n_wins']}/{m['n_losses']}"
        )

    # 回测结果
    if backtest_results and "error" not in backtest_results:
        print(f"\n📈 回测引擎结果 (fixed_horizon={list(backtest_results.keys())[0] if backtest_results else 'N/A'}):")
        for h, bt in backtest_results.items():
            if "error" in bt:
                print(f"   {h}日: {bt['error']}")
                continue
            print(f"   {h}日周期:")
            print(f"     总收益率: {bt['total_return_pct']:+.2f}%")
            print(f"     年化收益: {bt['annualized_return_pct']:+.2f}%")
            print(f"     夏普比率: {bt['sharpe_ratio']:.2f}")
            print(f"     最大回撤: {bt['max_drawdown_pct']:.2f}%")
            print(f"     卡玛比率: {bt['calmar_ratio']:.2f}")
            print(f"     胜率: {bt['win_rate_pct']:.1f}%")
            print(f"     盈亏比: {bt['profit_factor']:.2f}")
            print(f"     交易次数: {bt['n_trades']}")
            print(f"     回测天数: {bt['n_days']}")

    # Top/Bottom 股票
    if eval_records:
        by_return = sorted(eval_records, key=lambda r: r.actual_return_pct, reverse=True)
        print("\n🏆 表现最好的 10 只 (扣成本后):")
        for r in by_return[:10]:
            pred_str = f"{r.pred_direction}({r.pred_return_pct:+.1f}%)" if r.pred_return_pct else "无预测"
            print(f"   {r.ticker} @ {r.eval_date} [{r.horizon_days}d]: 实际 {r.actual_return_pct:+.2f}% ({r.actual_direction}), 预测 {pred_str}")

        print("\n📉 表现最差的 10 只:")
        for r in by_return[-10:]:
            pred_str = f"{r.pred_direction}({r.pred_return_pct:+.1f}%)" if r.pred_return_pct else "无预测"
            print(f"   {r.ticker} @ {r.eval_date} [{r.horizon_days}d]: 实际 {r.actual_return_pct:+.2f}% ({r.actual_direction}), 预测 {pred_str}")

    print("\n" + "=" * 80)


def save_report(
    eval_records: list[EvalRecord],
    horizon_metrics: dict,
    backtest_results: dict,
    from_date: str,
    to_date: str,
    output_file: str,
) -> None:
    """保存报告到 JSON"""
    report = {
        "date_range": {"from": from_date, "to": to_date},
        "generated_at": datetime.now().isoformat(),
        "total_records": len(eval_records),
        "horizon_metrics": horizon_metrics,
        "backtest_results": backtest_results,
        "records": [
            {
                "ticker": r.ticker,
                "eval_date": r.eval_date,
                "horizon_days": r.horizon_days,
                "pred_direction": r.pred_direction,
                "pred_return_pct": r.pred_return_pct,
                "actual_return_pct": r.actual_return_pct,
                "actual_direction": r.actual_direction,
                "is_direction_correct": r.is_direction_correct,
                "ta_signal": r.ta_signal,
                "composite_score": r.composite_score,
            }
            for r in eval_records
        ],
    }
    Path(output_file).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"💾 报告已保存: {output_file}")


def main():
    parser = argparse.ArgumentParser(description="每日流程结果历史回测验证")
    parser.add_argument("--from", dest="from_date", default="2026-10-01", help="起始日期 YYYY-MM-DD")
    parser.add_argument("--to", dest="to_date", default=None, help="截止日期 YYYY-MM-DD (默认最近交易日)")
    parser.add_argument("--tickers", default=None, help="只评估指定股票（逗号分隔）")
    parser.add_argument("--horizons", default="5,10,20", help="预测周期（天，逗号分隔）")
    parser.add_argument("--backtest", action="store_true", help="运行回测引擎")
    parser.add_argument("--output", default=None, help="输出报告文件路径")
    parser.add_argument("--output-dir", default="outputs/results", help="输出目录")
    args = parser.parse_args()

    from_date = args.from_date
    to_date = args.to_date or datetime.now().strftime("%Y-%m-%d")
    horizons = [int(h) for h in args.horizons.split(",")]
    tickers = [t.strip() for t in args.tickers.split(",")] if args.tickers else None

    print(f"🔍 开始历史回测验证: {from_date} ~ {to_date}")
    print(f"   预测周期: {horizons}")
    if tickers:
        print(f"   限制股票: {tickers}")

    # 1. 构建评估记录
    eval_records = build_eval_records(from_date, to_date, tickers, horizons)
    if not eval_records:
        print("❌ 无可评估记录")
        return 1

    # 2. 计算指标
    horizon_metrics = compute_metrics_by_horizon(eval_records, horizons)

    # 3. 回测引擎
    backtest_results = {}
    if args.backtest:
        for h in horizons:
            print(f"\n📈 运行 {h} 日周期回测...")
            backtest_results[h] = run_backtest_for_records(eval_records, h)

    # 4. 打印报告
    print_report(eval_records, horizon_metrics, backtest_results, from_date, to_date)

    # 5. 保存报告
    if args.output:
        output_file = args.output
    else:
        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_file = output_dir / f"historical_backtest_{from_date}_{to_date}.json"

    save_report(eval_records, horizon_metrics, backtest_results, from_date, to_date, output_file)

    return 0


if __name__ == "__main__":
    sys.exit(main())
