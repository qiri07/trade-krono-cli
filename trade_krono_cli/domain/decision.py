"""Decision — 投资决策领域对象。

InvestmentDecision 是 pipeline 的终点：融合 SignalAssessment + RiskAssessment，
产出最终的交易决策。

设计原则（V0.3 semantic upgrade）：
  - primary: expected_value / prob_win / risk_adjusted_ev  —— 真正的金融含义
  - auxiliary: ranking_score（原 composite_score 降级）—— 仅用于辅助排序
  - backward compat: composite_score 保留为 ranking_score 的别名
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from trade_krono_cli.domain.risk import RiskAssessment
from trade_krono_cli.domain.signal import SignalAssessment
from trade_krono_cli.domain.types import Signal


@dataclass(frozen=True)
class InvestmentDecision:
    """最终投资决策（V0.3 语义升级）。

    决策层级的核心指标（按金融意义重要性排序）：
      1. expected_value     — EV（%），P(win)×Gain − P(loss)×Loss − cost
      2. prob_win           — 盈利概率
      3. risk_adjusted_ev   — EV / vol_proxy（类 Sharpe）
      4. ranking_score      — 0-100 辅助排序分（原 composite_score 降级）

    Fields
    ------
    ticker                股票代码
    eval_date             决策日期
    signal                最终信号（BUY / HOLD / SELL）
    confidence            最终置信度 0-100

    # Expected Value（核心金融指标）
    expected_value        EV（%）—— P(up)×Gain − P(down)×Loss − cost
    prob_win              P(收益 > 0)
    risk_adjusted_ev      EV / vol_proxy（类 Sharpe 比率）

    # 来源引用（保持可追溯性）
    signal_assessment     信号评估（含 EV 信息）
    risk_assessment       风险评估

    # 交易执行参数
    position_size         建议仓位比例 0-1
    entry_zone            入场价区间
    target_price          目标价
    stop_loss             止损价
    horizon               投资周期（交易日）

    # 论点与风险
    thesis                综合投资论点
    risks                 风险清单
    invalidations         失效条件列表

    # 元数据（辅助）
    ranking_score         辅助排序分 0-100（原 composite_score 降级）
    job_id                关联的研究作业 ID
    """

    ticker: str
    eval_date: str
    signal: Signal
    confidence: float

    # ── Expected Value（核心金融指标）─────────────────────────────────────
    expected_value: float | None = None
    prob_win: float | None = None
    risk_adjusted_ev: float | None = None

    # 来源引用
    signal_assessment: SignalAssessment | None = None
    risk_assessment: RiskAssessment | None = None

    # 交易执行
    position_size: float | None = None
    entry_zone: list[float] | None = None
    target_price: float | None = None
    stop_loss: float | None = None
    horizon: int | None = None

    # 论点与风险
    thesis: str = ""
    risks: list[str] = field(default_factory=list)
    invalidations: list[str] = field(default_factory=list)

    # 元数据（辅助排序，金融意义次于 EV 指标）
    ranking_score: float | None = None
    job_id: str = ""

    # ── 序列化 ──────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        d: dict[str, Any] = {
            "ticker": self.ticker,
            "eval_date": self.eval_date,
            "signal": self.signal.value,
            "confidence": self.confidence,
            # Primary: EV metrics
            "expected_value": self.expected_value,
            "prob_win": self.prob_win,
            "risk_adjusted_ev": self.risk_adjusted_ev,
            # Auxiliary: ranking score
            "ranking_score": self.ranking_score,
            # 向后兼容 key
            "composite_score": self.ranking_score,
            "position_size": self.position_size,
            "entry_zone": self.entry_zone,
            "target_price": self.target_price,
            "stop_loss": self.stop_loss,
            "horizon": self.horizon,
            "thesis": self.thesis,
            "risks": self.risks,
            "invalidations": self.invalidations,
            "job_id": self.job_id,
        }
        if self.signal_assessment:
            d["signal_assessment"] = self.signal_assessment.to_dict()
        if self.risk_assessment:
            d["risk_assessment"] = self.risk_assessment.to_dict()
        return d

    @classmethod
    def from_dict(cls, data: dict) -> InvestmentDecision:
        sa_data = data.get("signal_assessment")
        sa = SignalAssessment.from_dict(sa_data) if sa_data else None
        ra_data = data.get("risk_assessment")
        ra = RiskAssessment.from_dict(ra_data) if ra_data else None

        sig_val = data.get("signal", "HOLD")
        if isinstance(sig_val, str):
            try:
                signal = Signal(sig_val)
            except ValueError:
                signal = Signal.HOLD
        else:
            signal = sig_val

        # 兼容旧版 composite_score 字段名
        ranking_score = data.get("ranking_score") or data.get("composite_score")

        return cls(
            ticker=data["ticker"],
            eval_date=data.get("eval_date", ""),
            signal=signal,
            confidence=float(data.get("confidence", 50.0)),
            expected_value=data.get("expected_value"),
            prob_win=data.get("prob_win"),
            risk_adjusted_ev=data.get("risk_adjusted_ev"),
            signal_assessment=sa,
            risk_assessment=ra,
            position_size=data.get("position_size"),
            entry_zone=data.get("entry_zone"),
            target_price=data.get("target_price"),
            stop_loss=data.get("stop_loss"),
            horizon=data.get("horizon"),
            thesis=data.get("thesis", ""),
            risks=data.get("risks", []),
            invalidations=data.get("invalidations", []),
            ranking_score=ranking_score,
            job_id=data.get("job_id", ""),
        )

    @classmethod
    def fallback(
        cls, ticker: str, eval_date: str, signal: Signal = Signal.HOLD, confidence: float = 50.0,
    ) -> InvestmentDecision:
        return cls(
            ticker=ticker,
            eval_date=eval_date,
            signal=signal,
            confidence=confidence,
        )

    def to_legacy_dict(self) -> dict:
        """转换为旧版 pipeline dict（向后兼容）。"""
        d: dict[str, Any] = {
            "ticker": self.ticker,
            "signal": self.signal.value,
            "confidence": self.confidence,
            "composite_score": self.ranking_score,  # 旧 key 名保留
            "expected_value": self.expected_value,
            "prob_win": self.prob_win,
            "risk_adjusted_ev": self.risk_adjusted_ev,
            "thesis": self.thesis,
            "risks": self.risks,
        }
        if self.signal_assessment:
            d["ta_signal"] = self.signal_assessment.ta.signal if self.signal_assessment.ta else None
            d["ta_confidence"] = (
                self.signal_assessment.ta.confidence if self.signal_assessment.ta else None
            )
            d["kronos_direction"] = (
                self.signal_assessment.kronos.direction.value
                if self.signal_assessment.kronos
                else None
            )
            d["kronos_change_pct"] = (
                self.signal_assessment.kronos.expected_return
                if self.signal_assessment.kronos
                else None
            )
            if self.signal_assessment.kronos:
                dist = self.signal_assessment.kronos.distribution
                d["kronos_prediction_uncertainty"] = dist.to_dict()
                d["prediction_distribution"] = dist.to_dict()
            d["expected_value"] = self.signal_assessment.expected_value
            d["conflict"] = self.signal_assessment.conflict
        if self.risk_assessment:
            d["risk_score_total"] = self.risk_assessment.risk_score_total
            d["adjusted_expected_return"] = self.risk_assessment.adjusted_expected_return
        return d
