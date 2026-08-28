"""
pipeline.merge — 结果合并 + 综合打分。

从 trade_krono_cli.merge 收敛而来，职责明确：
  - default_scorer   ：综合打分函数（满分 100），输出 ranking_score
  - merge_results    ：TA + Kronos 结果合并 + 约束 + 风险评分 + EV 计算
  - filter_pool      ：按信号/置信度过滤股票池
  - run_risk_assessment ：单只股票风险引擎

V0.3 语义升级：
  - ranking_score（原 composite_score）：辅助排序分 0-100
  - expected_value：P(up)×Gain − P(down)×Loss − cost（主要决策依据）
  - sort_primary   ：按 expected_value 降序（金融意义优先）
  - sort_secondary ：按 ranking_score 降序（辅助）
"""

from __future__ import annotations

from typing import Callable, Optional

import pandas as pd
from loguru import logger

from trade_krono_cli.configs.risk import RiskConfig
from trade_krono_cli.configs.scoring import ScoringConfig, ScoringStrategyConfig
from trade_krono_cli.constraints_config import ConstraintConfig
from trade_krono_cli.domain.signal import _compute_ev as _domain_compute_ev
from trade_krono_cli.kronos_runner import KronosForecastResult
from trade_krono_cli.risk.models import adjust_expected_return
from trade_krono_cli.risk.risk_engine import RiskEngine
from trade_krono_cli.scoring.registry import get_scorer_registry
from trade_krono_cli.ta_runner import StockAnalysisResult
from trade_krono_cli.trading_constraints import (
    T1Tracker,
    check_all_constraints,
)

REASONING_TRUNCATE_LEN = 500
DEFAULT_COST_BPS = 17.0


def _uncertainty_confidence_bonus(pu: Optional[dict], scoring: ScoringConfig) -> float:
    """基于预测不确定性的置信度加分/减分。"""
    if not pu:
        return 0.0
    cs = pu.get("confidence_score")
    if cs is None:
        return 0.0
    if cs >= scoring.uncertainty_high_threshold:
        return scoring.uncertainty_high_bonus
    elif cs >= scoring.uncertainty_med_threshold:
        return scoring.uncertainty_med_bonus
    else:
        return scoring.uncertainty_low_penalty


def _compute_ev_for_merged(
    kronos: Optional[KronosForecastResult],
    cost_bps: float = DEFAULT_COST_BPS,
) -> tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
    """
    从 KronosForecastResult 计算 EV 指标（委托给领域层 _compute_ev）。

    Returns
    -------
    (prob_win, prob_loss, expected_value, risk_adjusted_ev)
    """
    if kronos is None:
        return None, None, None, None

    ret = kronos.expected_change_pct
    if ret is None:
        return None, None, None, None
    try:
        ret = float(ret)
        if not (ret == ret):  # NaN check
            return None, None, None, None
    except (TypeError, ValueError):
        return None, None, None, None

    dist = getattr(kronos, "prediction_distribution", None)
    if dist is not None and type(dist).__name__ == "MagicMock":
        dist = None
    p10 = float(getattr(dist, "p10", None)) if dist is not None else None  # type: ignore[arg-type]
    p90 = float(getattr(dist, "p90", None)) if dist is not None else None  # type: ignore[arg-type]

    prob_win, prob_loss, _, _, ev, raev = _domain_compute_ev(
        direction=None,
        expected_return=ret,
        p10=p10,
        p90=p90,
        cost_bps=cost_bps,
    )
    return prob_win, prob_loss, ev, raev


def default_scorer(merged: dict, scoring: Optional[ScoringConfig] = None) -> float:
    """
    综合打分（满分 100），输出 ranking_score。

    ranking_score 是辅助排序分，不是收益率、Alpha、Sharpe 或 EV。
    真正的决策依据是 merged["expected_value"]。
    """
    s = scoring or ScoringConfig()

    raw_score = 0.0

    ta_conf = merged.get("ta_confidence") or 0
    raw_score += s.ta_confidence_weight * max(0, min(100, ta_conf))

    adj_ret = merged.get("adjusted_expected_return")
    if adj_ret is not None:
        chg = adj_ret
    else:
        chg = merged.get("kronos_change_pct") or merged.get("kronos_change_pct_gross") or 0
    raw_score += s.change_pct_weight * max(0, min(100, chg + s.change_pct_offset))

    direction = merged.get("kronos_direction")
    if direction == "UP":
        raw_score += s.direction_base_weight * s.direction_bonus_point
    elif direction == "DOWN":
        raw_score += s.direction_base_weight * (-s.direction_bonus_point)

    pu = merged.get("kronos_prediction_uncertainty")
    if pu:
        cs = pu.get("confidence_score") or 0
        raw_score += s.uncertainty_base_weight * max(0, min(100, cs))
        raw_score += _uncertainty_confidence_bonus(pu, s)

    if merged.get("adjusted_expected_return") is None:
        total_risk = merged.get("risk_score_total", 0) or 0
        risk_penalty = (total_risk / 100.0) * s.risk_penalty_weight * 100
        raw_score -= risk_penalty

    return round(max(0, min(100, raw_score)), 2)


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
        # V0.3: ranking_score（原 composite_score 降级）
        "ranking_score": None,
        # 向后兼容 key
        "composite_score": None,
        "forecast_dict": kronos.forecast_dict if kronos else None,
        "risk_score_total": None,
        "risk_scores": None,
        "risk_metrics": None,
        "adjusted_expected_return": None,
        # V0.3: EV 指标
        "expected_value": None,
        "prob_win": None,
        "risk_adjusted_ev": None,
    }


def run_risk_assessment(
    ticker: str,
    date: str,
    kline_df: pd.DataFrame,
    quote_data: Optional[dict] = None,
    ta_result: Optional[StockAnalysisResult] = None,
    risk_config: Optional[RiskConfig] = None,
) -> tuple[float, dict, dict]:
    """对单只股票运行风险引擎。"""
    engine = RiskEngine(risk_config=risk_config)
    risk_score, risk_metrics = engine.assess(ticker, date, kline_df, quote_data, ta_result)

    scores = {
        "volatility": risk_score.volatility_score,
        "drawdown": risk_score.drawdown_score,
        "liquidity": risk_score.liquidity_score,
        "concentration": risk_score.concentration_score,
        "market_regime": risk_score.market_regime_score,
        "gap_risk": risk_metrics.gap_risk_score,
        "event_risk": risk_metrics.event_risk_score,
        "valuation_risk": risk_metrics.valuation_risk_score,
    }

    metrics_dict = risk_metrics.to_dict()
    return risk_score.total_risk, scores, metrics_dict


def merge_results(
    ta_results: list[StockAnalysisResult],
    kronos_results: list[KronosForecastResult],
    scorer: Optional[Callable[..., float]] = None,
    kline_data: Optional[dict[str, pd.DataFrame]] = None,
    quote_data: Optional[dict[str, dict]] = None,
    constraints_config: Optional[ConstraintConfig] = None,
    t1_tracker: Optional[T1Tracker] = None,
    scoring_config: Optional[ScoringConfig] = None,
    risk_config: Optional[RiskConfig] = None,
    scoring_strategy: Optional[ScoringStrategyConfig] = None,
    degrade_mode: str = "strict",
) -> list[dict]:
    """
    将 TA 分析结果和 Kronos 预测结果合并。

    V0.3 排序逻辑：
      primary  ：expected_value 降序（金融意义最大）
      secondary：ranking_score 降序（辅助排序）
    """
    if scorer is None:
        from trade_krono_cli.scoring.scorers import LinearScorer

        if scoring_strategy:
            registry = get_scorer_registry()
            registered = registry.get(scoring_strategy.strategy)
            if registered:
                scorer = registered.score
                logger.debug(f"📊 使用打分策略: {registered.name}")
            else:
                scorer = LinearScorer().score
                logger.warning(
                    f"⚠️  未找到打分策略 '{scoring_strategy.strategy}'，fallback 到 linear"
                )
        else:
            scorer = LinearScorer().score

    if constraints_config is None:
        constraints_config = ConstraintConfig()

    kronos_map = {r.ticker: r for r in kronos_results if r.error is None}
    kline_map = kline_data or {}
    quote_map = quote_data or {}

    merged = []
    for ta in ta_results:
        kr = kronos_map.get(ta.ticker)
        item = _make_empty_merged(ta.ticker, ta, kr)

        # ── 降级模式标记 ──────────────────────────────────────────
        item["degradation_mode"] = None
        if degrade_mode == "ta_only_on_kronos_fail":
            if ta.error is None and (kr is None or kr.error is not None):
                item["degradation_mode"] = "kronos_degraded"
                logger.info(f"⚠️  {ta.ticker} Kronos 不可用，降级为「仅 TA 评分」模式")

        # ── A 股交易约束检查 ──────────────────────────────────────
        if constraints_config.enable_limit_check or constraints_config.enable_t1:
            prev_close = kr.last_close if kr else None
            pred_close = kr.predicted_close_final if kr else None
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

            if not constraint_result.allowed:
                item["ta_signal"] = "HOLD"
                item["ta_confidence"] = 0.0
                logger.info(f"🚫 {tk} 被约束拦截: {constraint_result.reason}")

        # ── 应用交易成本模型 ──────────────────────────────────────
        if kr and kr.expected_change_pct is not None and constraints_config.enable_cost_model:
            gross = kr.expected_change_pct
            net = constraints_config.apply_roundtrip_cost(gross)
            item["kronos_change_pct_gross"] = gross
            item["kronos_change_pct"] = round(net, 3)
            item["cost_bps_applied"] = constraints_config.total_roundtrip_bps()
        else:
            item["kronos_change_pct_gross"] = kr.expected_change_pct if kr else None
            item["cost_bps_applied"] = 0.0

        # ── 风险引擎 ──────────────────────────────────────────────
        tk = ta.ticker
        if tk in kline_map:
            try:
                total_risk, risk_scores, risk_metrics = run_risk_assessment(
                    ticker=tk,
                    date=ta.date,
                    kline_df=kline_map[tk],
                    quote_data=quote_map.get(tk),
                    ta_result=ta,
                    risk_config=risk_config,
                )
                item["risk_score_total"] = total_risk
                item["risk_scores"] = risk_scores
                item["risk_metrics"] = risk_metrics

                raw_ret = item.get("kronos_change_pct") or item.get("kronos_change_pct_gross")
                if raw_ret is not None and risk_metrics.get("return_adjustment") is not None:
                    item["adjusted_expected_return"] = adjust_expected_return(raw_ret, risk_metrics)
            except (ValueError, TypeError, KeyError, IndexError) as e:
                logger.warning(f"⚠️  风险评估异常 {tk}: {str(e)[:200]}")
                item["risk_score_total"] = 50.0
                item["risk_scores"] = {}
                item["risk_metrics"] = {}
                item["adjusted_expected_return"] = None
        else:
            item["risk_score_total"] = None
            item["risk_scores"] = None
            item["risk_metrics"] = None
            item["adjusted_expected_return"] = None

        # ── 计算 ranking_score ────────────────────────────────────
        item["ranking_score"] = scorer(item)
        # 向后兼容：同时写入 composite_score key
        item["composite_score"] = item["ranking_score"]

        # ── 计算 EV 指标 ──────────────────────────────────────────
        cost_bps = item.get("cost_bps_applied", DEFAULT_COST_BPS)
        prob_win, prob_loss, ev, raev = _compute_ev_for_merged(kr, cost_bps=cost_bps)
        item["expected_value"] = ev
        item["prob_win"] = prob_win
        item["risk_adjusted_ev"] = raev

        merged.append(item)

    # V0.3: 主要按 expected_value 降序，次要按 ranking_score 降序
    merged.sort(
        key=lambda x: (
            x.get("expected_value") or float("-inf"),
            x.get("ranking_score") or 0,
        ),
        reverse=True,
    )
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
) -> list[StockAnalysisResult]:
    """按信号 + 置信度过滤出可行股票池。"""
    pool: list[StockAnalysisResult] = []
    for r in ta_results:
        if r.error:
            continue
        if r.signal in allowed_signals and (r.confidence or 0) >= min_confidence:
            pool.append(r)
    logger.info(f"📋 过滤后股票池: {len(pool)} 只")
    for p in pool:
        logger.info(f"   • {p.ticker}: {p.signal} 置信度 {p.confidence}")
    return pool
