"""
结果合并 + 综合打分。
"""
from __future__ import annotations

from typing import Callable, Optional

import pandas as pd
from loguru import logger
from trade_krono_cli.ta_runner import StockAnalysisResult
from trade_krono_cli.kronos_runner import KronosForecastResult
from trade_krono_cli.risk.risk_engine import RiskEngine, RiskScore
from trade_krono_cli.trading_constraints import (
    T1Tracker,
    TradingConstraintResult,
    check_all_constraints,
    filter_by_constraints,
)
from trade_krono_cli.constraints_config import ConstraintConfig

# Truncation lengths for summary output
REASONING_TRUNCATE_LEN = 500
POOL_REASONING_TRUNCATE_LEN = 300


# ═══════════════════════════════════════════════════════
# 综合打分
# ═══════════════════════════════════════════════════════

_RISK_PENALTY_WEIGHT = 0.15  # 风险惩罚在总分中的最大占比（15%）


def default_scorer(merged: dict) -> float:
    """
    综合打分（满分 100）。

    权重：
      TA 置信度        40%
      预期涨跌幅       30%
      方向加成         10%
      预测不确定性     10%
      风险惩罚        15%   （新增：高风险股票扣分）

    风险惩罚逻辑：
      总风险分 0-100 → 惩罚力度 0~15 分（线性映射）
      即：高风险股票的综合得分最多被扣 15 分
    """
    score = 0.0

    # TA 部分（0-40）
    ta_conf = merged.get("ta_confidence") or 0
    score += 0.4 * max(0, min(100, ta_conf))

    # 预期涨跌幅（0-30，-50%~+50% → 0~100 → 0~30）
    # 优先使用扣除成本后的净收益
    chg = merged.get("kronos_change_pct") or merged.get("kronos_change_pct_gross") or 0
    score += 0.3 * max(0, min(100, chg + 50))

    # 方向加成（-5 ~ +5）
    direction = merged.get("kronos_direction")
    if direction == "UP":
        score += 0.1 * 10   # +1
    elif direction == "DOWN":
        score += 0.1 * (-10)  # -1

    # 预测不确定性加成（0-10）
    pu = merged.get("kronos_prediction_uncertainty")
    if pu:
        cs = pu.get("confidence_score") or 0
        score += 0.1 * max(0, min(100, cs))

    # 风险惩罚（0~15，高风险 → 扣分多）
    total_risk = merged.get("risk_score_total", 0) or 0
    risk_penalty = (total_risk / 100.0) * _RISK_PENALTY_WEIGHT * 100
    score -= risk_penalty

    return round(max(0, min(100, score)), 2)


# ═══════════════════════════════════════════════════════
# 合并函数
# ═══════════════════════════════════════════════════════

def _make_empty_merged(
    ticker: str,
    ta: Optional[StockAnalysisResult],
    kronos: Optional[KronosForecastResult],
) -> dict:
    pu = None
    if kronos and kronos.prediction_uncertainty:
        pu = (
            kronos.prediction_uncertainty.to_dict()
            if hasattr(kronos.prediction_uncertainty, "to_dict")
            else kronos.prediction_uncertainty
        )

    return {
        "ticker": ticker,
        "ta_signal": ta.signal if ta else None,
        "ta_confidence": ta.confidence if ta else None,
        "ta_reasoning": (ta.reasoning or "")[:REASONING_TRUNCATE_LEN] if ta else "",
        "ta_reports": (ta.reports or {}) if ta else {},
        "ta_error": ta.error if ta else None,
        "kronos_direction": kronos.direction if kronos else None,
        "kronos_change_pct": kronos.expected_change_pct if kronos else None,
        "kronos_volatility": kronos.volatility_proxy if kronos else None,
        "kronos_last_close": kronos.last_close if kronos else None,
        "kronos_pred_close": kronos.predicted_close_final if kronos else None,
        "kronos_confidence_band": kronos.confidence_band if kronos else None,
        "kronos_prediction_uncertainty": pu,
        "kronos_error": kronos.error if kronos else None,
        "composite_score": None,
        "forecast_dict": kronos.forecast_dict if kronos else None,
        # Risk Engine 字段
        "risk_score_total": None,
        "risk_scores": None,
    }


def run_risk_assessment(
    ticker: str,
    date: str,
    kline_df: pd.DataFrame,
    quote_data: Optional[dict] = None,
    ta_result: Optional[StockAnalysisResult] = None,
) -> tuple[float, dict]:
    """
    对单只股票运行风险引擎，返回 (total_risk, risk_scores_dict)。

    Parameters
    ----------
    ticker      : 股票代码
    date        : 评估日期
    kline_df    : K 线 DataFrame
    quote_data  : 实时估值数据（可选）
    ta_result   : TA 分析结果（可选）

    Returns
    -------
    (total_risk, risk_scores)
      total_risk : 综合风险分 0-100
      risk_scores : {volatility, drawdown, liquidity, concentration, market_regime}
    """
    engine = RiskEngine()
    risk = engine.assess(ticker, date, kline_df, quote_data, ta_result)
    scores = {
        "volatility": risk.volatility_score,
        "drawdown": risk.drawdown_score,
        "liquidity": risk.liquidity_score,
        "concentration": risk.concentration_score,
        "market_regime": risk.market_regime_score,
    }
    return risk.total_risk, scores


def merge_results(
    ta_results: list[StockAnalysisResult],
    kronos_results: list[KronosForecastResult],
    scorer: Optional[Callable[..., float]] = None,
    kline_data: Optional[dict[str, pd.DataFrame]] = None,
    quote_data: Optional[dict[str, dict]] = None,
    constraints_config: Optional[ConstraintConfig] = None,
    t1_tracker: Optional[T1Tracker] = None,
) -> list[dict]:
    """
    将 TA 分析结果和 Kronos 预测结果合并。

    Parameters
    ----------
    ta_results     : TA 分析结果列表
    kronos_results : Kronos 预测结果列表
    scorer         : 自定义打分函数，默认为 default_scorer
    kline_data     : {ticker: kline_df} 字典（可选，用于风险引擎）
    quote_data     : {ticker: quote_dict} 字典（可选，用于流动性风险计算）
    constraints_config : A 股交易约束配置（可选，默认启用全部约束）
    t1_tracker     : T+1 买入跟踪器（可选，跨股票共享）

    Returns
    -------
    排序后的综合结果列表（按 composite_score 降序）
    """
    if scorer is None:
        scorer = default_scorer

    if constraints_config is None:
        constraints_config = ConstraintConfig()

    kronos_map = {r.ticker: r for r in kronos_results if r.error is None}
    kline_map = kline_data or {}
    quote_map = quote_data or {}

    merged = []
    for ta in ta_results:
        kr = kronos_map.get(ta.ticker)
        item = _make_empty_merged(ta.ticker, ta, kr)

        # ── A 股交易约束检查 ──────────────────────────────────────
        if constraints_config.enable_limit_check or constraints_config.enable_t1:
            prev_close = kr.last_close if kr else None
            pred_close = kr.predicted_close_final if kr else None
            # 从 K 线提取最新收盘价作为当前参考价
            tk = ta.ticker
            if tk in kline_map and not prev_close:
                df = kline_map[tk]
                if len(df) > 0:
                    prev_close = float(df["close"].iloc[-1])
                    if pred_close is None:
                        pred_close = prev_close

            constraint_result = check_all_constraints(
                ticker=tk,
                eval_date=ta.date,
                current_price=pred_close,
                prev_close=prev_close,
                kline_df=kline_map.get(tk),
                t1_tracker=t1_tracker,
                config=constraints_config,
            )
            item["constraint_allowed"] = constraint_result.allowed
            item["constraint_reason"] = constraint_result.reason
            item["constraint_is_st"] = constraint_result.is_st
            item["constraint_limit_up"] = constraint_result.limit_up_price
            item["constraint_limit_down"] = constraint_result.limit_down_price

            # 若被约束拦截，标记信号为 HOLD 并记录原因
            if not constraint_result.allowed:
                item["ta_signal"] = "HOLD"
                item["ta_confidence"] = 0.0
                logger.info(
                    f"🚫 {tk} 被约束拦截: {constraint_result.reason}"
                )

        # ── 应用交易成本模型 ──────────────────────────────────────
        if (
            kr
            and kr.expected_change_pct is not None
            and constraints_config.enable_cost_model
        ):
            gross = kr.expected_change_pct
            net = constraints_config.apply_roundtrip_cost(gross)
            item["kronos_change_pct_gross"] = gross
            item["kronos_change_pct"] = round(net, 3)
            item["cost_bps_applied"] = constraints_config.total_roundtrip_bps()
        else:
            item["kronos_change_pct_gross"] = (
                kr.expected_change_pct if kr else None
            )
            item["cost_bps_applied"] = 0.0

        # 运行风险引擎（若有 K 线数据）
        tk = ta.ticker
        if tk in kline_map:
            try:
                total_risk, risk_scores = run_risk_assessment(
                    ticker=tk,
                    date=ta.date,
                    kline_df=kline_map[tk],
                    quote_data=quote_map.get(tk),
                    ta_result=ta,
                )
                item["risk_score_total"] = total_risk
                item["risk_scores"] = risk_scores
            except Exception as e:
                logger.warning(f"⚠️  风险评估失败 {tk}: {e}")
                item["risk_score_total"] = 50.0
                item["risk_scores"] = {}
        else:
            item["risk_score_total"] = None
            item["risk_scores"] = None

        item["composite_score"] = scorer(item)
        merged.append(item)

    merged.sort(key=lambda x: (x.get("composite_score") or 0), reverse=True)

    for i, item in enumerate(merged, 1):
        item["rank"] = i

    logger.info(
        f"📊 合并完成: {len(merged)} 只股票，"
        f"TA 成功 {sum(1 for r in ta_results if r.error is None)}/{len(ta_results)}, "
        f"Kronos 成功 {len(kronos_map)}/{len(kronos_results)}"
    )

    return merged


def filter_pool(
    ta_results: list[StockAnalysisResult],
    min_confidence: float = 55.0,
    allowed_signals: tuple[str, ...] = ("BUY", "HOLD"),
) -> list[dict]:
    """
    按信号 + 置信度过滤出可行股票池。
    """
    pool = []
    for r in ta_results:
        if r.error:
            continue
        if r.signal in allowed_signals and (r.confidence or 0) >= min_confidence:
            pool.append({
                "ticker": r.ticker,
                "ta_signal": r.signal,
                "ta_confidence": r.confidence,
                "ta_reasoning": (r.reasoning or "")[:POOL_REASONING_TRUNCATE_LEN],
                "ta_reports": r.reports,
                "ta_result": r,
            })
    logger.info(f"📋 过滤后股票池: {len(pool)} 只")
    for p in pool:
        logger.info(f"   • {p['ticker']}: {p['ta_signal']} 置信度 {p['ta_confidence']}")
    return pool
