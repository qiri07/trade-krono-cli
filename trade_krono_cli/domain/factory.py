"""Factory — 领域对象的工厂函数。

提供从原始数据/旧对象构建领域对象的标准接口。
Pipeline 应通过工厂函数创建领域对象，而非直接构造。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from trade_krono_cli.domain import Signal
from trade_krono_cli.domain.decision import InvestmentDecision
from trade_krono_cli.domain.evaluation import EvalRecord
from trade_krono_cli.domain.signal import (
    SignalAssessment,
    _compute_ev,
    detect_conflict,
)
from trade_krono_cli.domain.types import Direction

if TYPE_CHECKING:
    from trade_krono_cli.domain.prediction import (
        KronosPrediction,
        TAAnalysis,
    )
    from trade_krono_cli.domain.risk import RiskAssessment

# ═══════════════════════════════════════════════════════
#  build_signal_assessment
# ═══════════════════════════════════════════════════════


def build_signal_assessment(
    ticker: str,
    eval_date: str,
    *,
    ta: TAAnalysis | None = None,
    kronos: KronosPrediction | None = None,
    committee_rec: Signal | None = None,
    committee_confidence: float | None = None,
    bull_case: str = "",
    bear_case: str = "",
    cost_bps: float = 17.0,
) -> SignalAssessment:
    """从 TA + Kronos + Committee 构建 SignalAssessment。

    自动完成：
      1. 多源信号融合（多数表决）
      2. 冲突检测
      3. EV 计算
    """
    # ── 融合信号 ──────────────────────────────────────────────────────
    votes: list[tuple[Signal, float, str]] = []
    if ta:
        votes.append((Signal(ta.signal), ta.confidence, "ta"))
    if kronos:
        k_sig = {"UP": Signal.BUY, "DOWN": Signal.SELL, "FLAT": Signal.HOLD}[kronos.direction.value]
        ks = kronos.distribution.direction_score or 0.5
        votes.append((k_sig, ks * 100, "kronos"))
    if committee_rec:
        votes.append((committee_rec, committee_confidence or 50.0, "committee"))

    final_signal, final_confidence = _majority_vote(votes)

    # ── 冲突检测 ──────────────────────────────────────────────────────
    ta_sig = Signal(ta.signal) if ta else None
    kr_dir = Direction(kronos.direction) if kronos and kronos.direction else None
    conflict = detect_conflict(ta_sig, kr_dir, committee_rec)

    # ── EV 计算 ───────────────────────────────────────────────────────
    ev_result = _compute_ev(
        direction=kronos.direction if kronos else None,
        expected_return=kronos.expected_return if kronos else 0.0,
        p10=kronos.p10 if kronos else None,
        p90=kronos.p90 if kronos else None,
        cost_bps=cost_bps,
    )
    prob_win, prob_loss, avg_win, avg_loss, ev, raev = ev_result

    # ── 综合论点 ──────────────────────────────────────────────────────
    thesis_parts = []
    if ta:
        thesis_parts.append(ta.thesis[:200])
    if bull_case:
        thesis_parts.append(f"Bull: {bull_case[:100]}")
    if bear_case:
        thesis_parts.append(f"Bear: {bear_case[:100]}")
    thesis = " | ".join(thesis_parts) if thesis_parts else ""

    risks = list(set(sum([ta.risks for ta in [ta] if ta], [])))

    return SignalAssessment(
        ticker=ticker,
        eval_date=eval_date,
        ta=ta,
        kronos=kronos,
        committee_rec=committee_rec,
        committee_confidence=committee_confidence,
        bull_case=bull_case,
        bear_case=bear_case,
        final_signal=final_signal,
        final_confidence=final_confidence,
        conflict=conflict,
        prob_win=prob_win,
        prob_loss=prob_loss,
        avg_win_return=avg_win,
        avg_loss_return=avg_loss,
        expected_value=ev,
        risk_adjusted_ev=raev,
        cost_bps=cost_bps,
        thesis=thesis,
        risks=risks,
    )


# ═══════════════════════════════════════════════════════
#  build_investment_decision
# ═══════════════════════════════════════════════════════


def build_investment_decision(
    ticker: str,
    eval_date: str,
    signal_assessment: SignalAssessment,
    risk_assessment: RiskAssessment | None = None,
    *,
    ranking_score: float | None = None,
    composite_score: float | None = None,  # 向后兼容别名
    job_id: str = "",
    position_size: float | None = None,
    entry_zone: list[float] | None = None,
    target_price: float | None = None,
    stop_loss: float | None = None,
    horizon: int | None = None,
) -> InvestmentDecision:
    """从 SignalAssessment + RiskAssessment 构建 InvestmentDecision。

    V0.3: 自动从 signal_assessment 传播 EV 指标到决策层。
    ranking_score 是辅助排序分（原 composite_score 降级）。
    """
    # 向后兼容：composite_score → ranking_score
    rs = ranking_score if ranking_score is not None else composite_score
    return InvestmentDecision(
        ticker=ticker,
        eval_date=eval_date,
        signal=signal_assessment.final_signal,
        confidence=signal_assessment.final_confidence,
        # 传播 EV 指标（primary decision metrics）
        expected_value=signal_assessment.expected_value,
        prob_win=signal_assessment.prob_win,
        risk_adjusted_ev=signal_assessment.risk_adjusted_ev,
        signal_assessment=signal_assessment,
        risk_assessment=risk_assessment,
        position_size=position_size,
        entry_zone=entry_zone,
        target_price=target_price,
        stop_loss=stop_loss,
        horizon=horizon,
        thesis=signal_assessment.thesis,
        risks=signal_assessment.risks,
        invalidations=signal_assessment.invalidations,
        ranking_score=rs,
        job_id=job_id,
    )


# ═══════════════════════════════════════════════════════
#  build_eval_record
# ═══════════════════════════════════════════════════════


def build_eval_record(
    ticker: str,
    eval_date: str,
    horizon_days: int,
    pred_direction: str | None,
    pred_return_pct: float | None,
    actual_return_pct: float,
    *,
    p10: float | None = None,
    p25: float | None = None,
    p50: float | None = None,
    p75: float | None = None,
    p90: float | None = None,
    ta_signal: str | None = None,
    ranking_score: float | None = None,
    composite_score: float | None = None,  # 向后兼容别名
    expected_value: float | None = None,
    conflict: str = "",
) -> EvalRecord:
    """从预测和实际结果构建 EvalRecord。"""
    # 向后兼容：composite_score → ranking_score
    rs = ranking_score if ranking_score is not None else composite_score
    actual_direction = (
        "UP" if actual_return_pct > 1.0 else ("DOWN" if actual_return_pct < -1.0 else "FLAT")
    )
    is_correct = (pred_direction == actual_direction) if pred_direction else False
    return EvalRecord(
        ticker=ticker,
        eval_date=eval_date,
        horizon_days=horizon_days,
        pred_direction=pred_direction,
        pred_return_pct=pred_return_pct,
        actual_return_pct=actual_return_pct,
        actual_direction=actual_direction,
        is_direction_correct=is_correct,
        error_pct=round((pred_return_pct or 0) - actual_return_pct, 4),
        p10=p10,
        p25=p25,
        p50=p50,
        p75=p75,
        p90=p90,
        ta_signal=ta_signal,
        ranking_score=rs,
        expected_value=expected_value,
        composite_score=rs,
        conflict=conflict,
    )


# ═══════════════════════════════════════════════════════
#  内部辅助
# ═══════════════════════════════════════════════════════


def _majority_vote(
    votes: list[tuple[Signal, float, str]],
) -> tuple[Signal, float]:
    """多数表决：返回 (最终信号, 综合置信度)。

    规则：
      · 三方一致 → 直接采用，confidence = min of all
      · 两方一致 vs 一方 dissent → 采用多数的，降低 confidence
      · 三方分歧 → HOLD，confidence = 33
    """
    if not votes:
        return Signal.HOLD, 50.0

    sig_counts: dict[Signal, int] = {}
    for sig, _, _ in votes:
        sig_counts[sig] = sig_counts.get(sig, 0) + 1
    final = max(sig_counts, key=sig_counts.get)  # type: ignore[arg-type]
    majority_count = sig_counts[final]

    total_weight = sum(w for _, w, _ in votes)
    weighted_conf = (
        sum(w for s, w, _ in votes if s == final) / total_weight * 100 if total_weight else 50
    )
    dissent_penalty = (3 - majority_count) * 10
    confidence = round(max(0, min(100, weighted_conf - dissent_penalty)), 1)

    return final, confidence


# 向后兼容：重导出旧名
build_signal = build_signal_assessment
build_decision = build_investment_decision
