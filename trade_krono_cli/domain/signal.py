"""Signal — 信号评估领域对象。

SignalAssessment 是多源信号（TA + Kronos + Committee）的融合结果，
并在此层计算 Expected Value（期望收益）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from trade_krono_cli.domain.prediction import KronosPrediction, TAAnalysis
from trade_krono_cli.domain.types import Direction
from trade_krono_cli.domain.types import Signal as DomainSignal

# ═══════════════════════════════════════════════════════
#  SignalConflict
# ═══════════════════════════════════════════════════════


class SignalConflict:
    """多源信号冲突标记（常量类，非 Enum，避免 str 基类的属性丢失问题）。"""

    NONE = "none"
    TA_vs_KRONOS = "ta_vs_kronos"
    TA_vs_COMMITTEE = "ta_vs_committee"
    KRONOS_vs_COMMITTEE = "kronos_vs_committee"
    ALL_CONFLICT = "all_conflict"

    CONFLICT_VALUES: frozenset = frozenset(
        {
            TA_vs_KRONOS,
            TA_vs_COMMITTEE,
            KRONOS_vs_COMMITTEE,
            ALL_CONFLICT,
        },
    )

    @staticmethod
    def is_conflict(value: str) -> bool:
        return value in SignalConflict.CONFLICT_VALUES


# ═══════════════════════════════════════════════════════
#  SignalAssessment
# ═══════════════════════════════════════════════════════


@dataclass(frozen=True)
class SignalAssessment:
    """多源信号融合 + EV 计算的评估结果。

    这是 pipeline 的核心中间对象：
      TAAnalysis + KronosPrediction + CommitteeResult
      → SignalAssessment（含 EV、冲突检测、最终信号）

    Fields
    ------
    ticker               股票代码
    eval_date            评估日期
    ta                   TA 分析结果（可为 None）
    kronos               Kronos 预测结果（可为 None）
    committee_rec        委员会推荐（可为 None）
    committee_confidence 委员会置信度
    bull_case            Bull Case 摘要
    bear_case            Bear Case 摘要

    # 融合结果
    final_signal         综合信号（多数表决）
    final_confidence     综合置信度 0–100
    conflict             冲突标记

    # Expected Value
    prob_win             P(收益 > 0)
    prob_loss            P(收益 < 0)
    avg_win_return       E[收益 | 收益 > 0]
    avg_loss_return      E[|收益| | 收益 < 0]
    expected_value       EV（%）
    risk_adjusted_ev     EV / vol_proxy（类 Sharpe）
    cost_bps             双边交易成本（bps）

    # 交易参数
    position_size        建议仓位比例 0–1
    entry_zone           入场区间
    target_price         目标价
    stop_loss            止损价
    horizon              投资周期
    """

    ticker: str
    eval_date: str
    ta: TAAnalysis | None = None
    kronos: KronosPrediction | None = None
    committee_rec: DomainSignal | None = None
    committee_confidence: float | None = None
    bull_case: str = ""
    bear_case: str = ""

    # 融合结果
    final_signal: DomainSignal = DomainSignal.HOLD
    final_confidence: float = 50.0
    conflict: str = SignalConflict.NONE

    # EV 指标
    prob_win: float | None = None
    prob_loss: float | None = None
    avg_win_return: float | None = None
    avg_loss_return: float | None = None
    expected_value: float | None = None
    risk_adjusted_ev: float | None = None
    cost_bps: float = 17.0

    # 交易参数
    position_size: float | None = None
    entry_zone: list[float] | None = None
    target_price: float | None = None
    stop_loss: float | None = None
    horizon: int | None = None

    # 综合论点
    thesis: str = ""
    risks: list[str] = field(default_factory=list)
    invalidations: list[str] = field(default_factory=list)

    # ── 序列化 ──────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        d: dict[str, Any] = {  # Any: dict contains mixed-tuple values from external Kronos scoring
            "ticker": self.ticker,
            "eval_date": self.eval_date,
            "final_signal": self.final_signal.value,
            "final_confidence": self.final_confidence,
            "conflict": self.conflict,
            "expected_value": self.expected_value,
            "risk_adjusted_ev": self.risk_adjusted_ev,
            "prob_win": self.prob_win,
            "position_size": self.position_size,
            "horizon": self.horizon,
            "thesis": self.thesis,
            "risks": self.risks,
        }
        if self.ta:
            d["ta_analysis"] = self.ta.to_dict()
        if self.kronos:
            d["kronos_prediction"] = self.kronos.to_dict()
        if self.committee_rec:
            d["committee_rec"] = self.committee_rec.value
            d["committee_confidence"] = self.committee_confidence
        if self.bull_case:
            d["bull_case"] = self.bull_case
        if self.bear_case:
            d["bear_case"] = self.bear_case
        if self.entry_zone:
            d["entry_zone"] = self.entry_zone
        if self.target_price:
            d["target_price"] = self.target_price
        if self.stop_loss:
            d["stop_loss"] = self.stop_loss
        return d

    @classmethod
    def from_dict(cls, data: dict) -> SignalAssessment:
        ta_data = data.get("ta_analysis")
        ta = TAAnalysis.from_dict(ta_data) if ta_data else None
        kronos_data = data.get("kronos_prediction")
        kronos = KronosPrediction.from_dict(kronos_data) if kronos_data else None

        sig_val = data.get("final_signal", "HOLD")
        if isinstance(sig_val, str):
            try:
                final_signal = DomainSignal(sig_val)
            except ValueError:
                final_signal = DomainSignal.HOLD
        else:
            final_signal = sig_val

        committee_val = data.get("committee_rec")
        if isinstance(committee_val, str):
            try:
                committee_rec = DomainSignal(committee_val)
            except ValueError:
                committee_rec = None
        else:
            committee_rec = committee_val

        return cls(
            ticker=data["ticker"],
            eval_date=data.get("eval_date", ""),
            ta=ta,
            kronos=kronos,
            committee_rec=committee_rec,
            committee_confidence=data.get("committee_confidence"),
            bull_case=data.get("bull_case", ""),
            bear_case=data.get("bear_case", ""),
            final_signal=final_signal,
            final_confidence=float(data.get("final_confidence", 50.0)),
            conflict=data.get("conflict", SignalConflict.NONE),
            prob_win=data.get("prob_win"),
            prob_loss=data.get("prob_loss"),
            avg_win_return=data.get("avg_win_return"),
            avg_loss_return=data.get("avg_loss_return"),
            expected_value=data.get("expected_value"),
            risk_adjusted_ev=data.get("risk_adjusted_ev"),
            cost_bps=float(data.get("cost_bps", 17.0)),
            position_size=data.get("position_size"),
            entry_zone=data.get("entry_zone"),
            target_price=data.get("target_price"),
            stop_loss=data.get("stop_loss"),
            horizon=data.get("horizon"),
            thesis=data.get("thesis", ""),
            risks=data.get("risks", []),
            invalidations=data.get("invalidations", []),
        )


# ═══════════════════════════════════════════════════════
#  辅助函数
# ═══════════════════════════════════════════════════════


def _compute_ev(
    direction: Direction | None,
    expected_return: float,
    p10: float | None,
    p90: float | None,
    cost_bps: float,
) -> tuple[float | None, float | None, float | None, float | None, float | None, float | None]:
    """基于分位数计算 EV 指标。

    Returns
    -------
    (prob_win, prob_loss, avg_win_return, avg_loss_return, expected_value)

    """
    if expected_return is None:
        return None, None, None, None, None, None

    ret = expected_return
    p10_val = p10 or (ret * -0.5 if ret > 0 else ret * 0.5)
    p90_val = p90 or (ret * 1.5 if ret > 0 else ret * 0.5)

    # 方向偏差
    bias = 0.2 if ret > 0 else (-0.2 if ret < 0 else 0.0)
    prob_win = round(max(0.05, min(0.95, 0.5 + bias)), 3)
    prob_loss = round(1.0 - prob_win, 3)

    # 收益区间中点
    avg_win = round((ret + p90_val) / 2, 4) if ret > 0 else round(p90_val, 4)
    avg_loss = round(abs((ret + p10_val) / 2), 4) if ret < 0 else round(abs(p10_val), 4)

    cost_pct = cost_bps / 100.0
    ev = prob_win * avg_win - prob_loss * avg_loss - cost_pct
    ev = round(ev, 4)

    # Risk-adjusted EV
    vol_proxy = abs(p90_val - p10_val) / 2 if p90_val != p10_val else abs(ret) * 0.5
    raev = round(ev / vol_proxy, 4) if vol_proxy > 1e-8 else 0.0

    return prob_win, prob_loss, avg_win, avg_loss, ev, raev


def detect_conflict(
    ta_signal: DomainSignal | None,
    kronos_direction: Direction | None,
    committee_rec: DomainSignal | None,
) -> str:
    """检测多源信号冲突。"""
    signals: list[tuple[str, DomainSignal]] = []
    if ta_signal:
        signals.append(("ta", ta_signal))
    if kronos_direction:
        k = {"UP": DomainSignal.BUY, "DOWN": DomainSignal.SELL, "FLAT": DomainSignal.HOLD}[
            kronos_direction.value
        ]
        signals.append(("kronos", k))
    if committee_rec:
        signals.append(("committee", committee_rec))

    if len(signals) < 2:
        return SignalConflict.NONE

    unique = {s.value for _, s in signals}
    if len(unique) == 1:
        return SignalConflict.NONE
    if len(unique) == 2:
        for a, b in [("ta", "kronos"), ("ta", "committee"), ("kronos", "committee")]:
            pair = [(k, s) for k, s in signals if k in (a, b)]
            if len(pair) == 2 and pair[0][1] != pair[1][1]:
                return f"{a}_vs_{b}"
    return SignalConflict.ALL_CONFLICT
