#!/usr/bin/env python3
"""combined_stock_analysis 技术分析与增强模块。

包含：
- analyze_candidates: 对候选股票做技术分析
- attach_kronos: 批量跑 Kronos 预测
- _build_eval_records: 构建回测记录（从 research DB 历史信号）
- _lookup_signal: 查询某 job 中某 ticker 的历史信号
- attach_backtest: 逐只单股回测
"""

from __future__ import annotations

from datetime import datetime, timedelta

from loguru import logger

from scripts._utils import normalize_ticker_key
from scripts.combined_stock_models import BacktestView, StockCandidate
from scripts.daily_analysis import analyze_stock
from trade_krono_cli.eval_data import EvalRecord
from trade_krono_cli.research_db import ResearchDatabase


def analyze_candidates(
    cands: list[StockCandidate], date_str: str, max_analyze: int
) -> list[StockCandidate]:
    """对候选做技术分析，仅保留有数据且评分较高的，按命中数+分数排序。"""
    analyzed: list[StockCandidate] = []
    for cand in cands[:max_analyze]:
        code = cand.code
        ticker = normalize_ticker_key(code)
        res = analyze_stock(ticker, end_date=date_str)
        if res.get("status") != "ok":
            logger.debug(f"  {code} 无数据，跳过")
            continue
        cand.analysis = res
        analyzed.append(cand)

    analyzed.sort(key=lambda x: -x.rank_score)
    return analyzed


def attach_kronos(cands: list[StockCandidate], date_str: str) -> int:
    """对候选批量跑 Kronos 预测，写回 cand.kronos，返回成功填充数量。

    懒加载 KronosSession（此时才导入/加载模型），批量预测走缓存+分批+降级；
    任何异常均不阻断主流程（log warning 后返回已填充数量）。
    """
    if not cands:
        return 0
    try:
        from trade_krono_cli.models.kronos_session import KronosSession  # noqa: PLC0415

        tickers = [normalize_ticker_key(c.code) for c in cands]
        session = KronosSession()
        results = session.runner.predict_batch(tickers, date_str)
        by_ticker = {r.ticker: r for r in results}
        n_ok = 0
        for cand in cands:
            res = by_ticker.get(normalize_ticker_key(cand.code))
            if res is not None and not res.error:
                cand.kronos = res
                n_ok += 1
        logger.info(f"🧠 Kronos 预测完成：{n_ok}/{len(cands)} 只成功")
        return n_ok
    except Exception as e:
        logger.warning(f"Kronos 预测整体失败（不影响推送）: {type(e).__name__}: {e}")
        return 0


def _build_eval_records(
    ticker: str, date_str: str, days: int, horizon: int
) -> list[EvalRecord]:
    """从 research DB 读取该 ticker 近 days 天的历史信号，构造 EvalRecord 列表。

    优先读 `ta_analysis` 表（独立分析脚本实际写入的表），`signals` 表（pipeline
    post_processing 写入）作为 fallback —— 二者取到即视为该日有信号。复用
    eval_data 的价格获取（±5 日回退）与成本扣减；无信号 / 无价格时返回空列表。
    按 (job, ticker) 粒度取每个 job 的信号，同一天多 job 仅取首个。
    """
    from trade_krono_cli.eval_data import (  # noqa: PLC0415
        apply_roundtrip_cost,
        calc_return,
        get_close_price,
    )
    from trade_krono_cli.research_db import get_research  # noqa: PLC0415

    research = get_research()
    jobs = research.list_jobs(limit=2000)
    window_start = (datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=days)).strftime(
        "%Y-%m-%d"
    )
    norm_ticker = normalize_ticker_key(ticker)

    records: list[EvalRecord] = []
    seen_dates: set[str] = set()
    for job in jobs:
        job_date = str(job.get("date") or "")
        if not job_date or job_date < window_start or job_date > date_str:
            continue
        if job_date in seen_dates:
            continue
        job_id = str(job.get("job_id") or "")
        if not job_id:
            continue

        sig = _lookup_signal(research, job_id, norm_ticker)
        if sig is None:
            continue
        if sig["has_error"]:
            continue
        seen_dates.add(job_date)

        entry_price = get_close_price(norm_ticker, job_date)
        if entry_price is None or entry_price <= 0:
            continue
        exit_date = (datetime.strptime(job_date, "%Y-%m-%d") + timedelta(days=horizon)).strftime(
            "%Y-%m-%d"
        )
        exit_price = get_close_price(norm_ticker, exit_date)
        if exit_price is None or exit_price <= 0:
            continue

        gross = calc_return(entry_price, exit_price)
        net = apply_roundtrip_cost(gross, 17.0)
        pred_dir = sig["kronos_direction"]
        pred_ret = sig["kronos_change"]
        actual_dir = "UP" if net > 1.0 else ("DOWN" if net < -1.0 else "FLAT")
        is_correct = bool(pred_dir) and pred_dir != "FLAT" and pred_dir == actual_dir
        error = (float(pred_ret) - gross) if isinstance(pred_ret, (int, float)) else 0.0

        records.append(
            EvalRecord(
                ticker=norm_ticker,
                eval_date=job_date,
                horizon_days=horizon,
                pred_direction=pred_dir,
                pred_return_pct=(float(pred_ret) if isinstance(pred_ret, (int, float)) else None),
                actual_return_pct=round(net, 4),
                actual_direction=actual_dir,
                is_direction_correct=is_correct,
                error_pct=round(error, 4),
                ta_signal=sig["ta_signal"],
                composite_score=(
                    float(sig["composite_score"])
                    if isinstance(sig["composite_score"], (int, float))
                    else None
                ),
            ),
        )
    return records


def _lookup_signal(research: ResearchDatabase, job_id: str, norm_ticker: str) -> dict | None:
    """取某 job 中某 ticker 的历史信号，统一为内部字段。

    优先 `ta_analysis`（独立分析脚本写入），fallback `signals`（pipeline 写入）。
    统一字段：ta_signal / kronos_direction / kronos_change / composite_score / has_error。
    """
    norm_sig = next(
        (
            s
            for s in research.get_ta_by_job(job_id)
            if normalize_ticker_key(str(s.get("ticker") or "")) == norm_ticker
        ),
        None,
    )
    if norm_sig is not None:
        has_error = bool(norm_sig.get("error"))
        return {
            "ta_signal": norm_sig.get("signal"),
            "kronos_direction": None,
            "kronos_change": None,
            "composite_score": None,
            "has_error": has_error,
        }

    sig = next(
        (
            s
            for s in research.get_signals_by_job(job_id)
            if normalize_ticker_key(str(s.get("ticker") or "")) == norm_ticker
        ),
        None,
    )
    if sig is not None:
        both_errors = bool(sig.get("ta_error")) and bool(sig.get("kronos_error"))
        return {
            "ta_signal": sig.get("ta_signal"),
            "kronos_direction": sig.get("kronos_direction") or None,
            "kronos_change": sig.get("kronos_change"),
            "composite_score": sig.get("composite_score"),
            "has_error": both_errors,
        }
    return None


def attach_backtest(
    cands: list[StockCandidate], date_str: str, days: int = 90, horizon: int = 5
) -> int:
    """逐只读取该 ticker 的历史信号并跑单股回测，写回 cand.backtest。

    返回成功填充数量（含「无信号」降级为 available=False 的也计入尝试，
    实际填充 available=True 的数量）。任何异常不阻断主流程。
    """
    if not cands:
        return 0
    n_ok = 0
    for cand in cands:
        try:
            from trade_krono_cli.prediction_eval_backtest import run_backtest  # noqa: PLC0415

            eval_records = _build_eval_records(cand.code, date_str, days, horizon)
            if not eval_records:
                cand.backtest = BacktestView(
                    horizon_days=horizon, window_days=days, available=False
                )
                continue
            bt = run_backtest(eval_records, rebal_mode="fixed_horizon", fixed_horizon=horizon)
            metrics = bt.metrics or {}
            has_signal_history = bool(eval_records)
            cand.backtest = BacktestView(
                total_return_pct=bt.total_return_pct if has_signal_history else None,
                sharpe_ratio=metrics.get("sharpe_ratio") or None,
                max_drawdown_pct=metrics.get("max_drawdown_pct") or None,
                win_rate_pct=metrics.get("win_rate_pct") or None,
                n_trades=bt.n_trades,
                n_signals=len(eval_records),
                horizon_days=horizon,
                window_days=days,
                available=has_signal_history,
            )
            if has_signal_history:
                n_ok += 1
        except Exception as e:
            logger.warning(f"  {cand.code} 历史回测失败: {type(e).__name__}: {e}")
            cand.backtest = BacktestView(horizon_days=horizon, window_days=days, available=False)
    logger.info(f"📈 历史回测完成：{n_ok}/{len(cands)} 只成功")
    return n_ok
