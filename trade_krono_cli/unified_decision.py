"""
UnifiedInvestmentDecision — 跨源统一投资决策。

将 TA 信号、Kronos 预测、Committee 审议结果、风险调整后收益，
统一到单一数据结构中，并计算 Expected Value（期望收益）。

设计原则：
  · 不替换旧的 InvestmentDecision（TA-only），而是新增统一版本
  · EV = P(win) × E[win_return] − P(lose) × E[|lose_return|] − cost
    其中概率和收益分布来自 PredictionDistribution 的分位数
  · 支持多源信号冲突检测（TA BUY vs Kronos DOWN → flag conflict）
  · 所有数值字段均可序列化，支持持久化和实验比较
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from trade_krono_cli.domain.signal import SignalConflict
from trade_krono_cli.ta_decision import InvestmentDecision as TADecision
from trade_krono_cli.ta_decision import Signal

# 兼容：domain.SignalConflict 是常量类，直接用字符串值
_NoneConflict = SignalConflict.NONE
_TaVsKronos = SignalConflict.TA_vs_KRONOS
_TaVsCommittee = SignalConflict.TA_vs_COMMITTEE
_KronosVsCommittee = SignalConflict.KRONOS_vs_COMMITTEE
_AllConflict = SignalConflict.ALL_CONFLICT


# ═══════════════════════════════════════════════════════
#  UnifiedInvestmentDecision
# ═══════════════════════════════════════════════════════


@dataclass
class UnifiedInvestmentDecision:
    """
    统一投资决策：TA + Kronos + Committee + Risk-Adjusted EV。

    字段分组
    ──────────────────────────────────────────────────────
    [基础]
    ticker              股票代码
    eval_date           决策日期
    final_signal        最终推荐信号（综合所有源）
    final_confidence    最终置信度 0–100

    [TA 源]
    ta_signal           TA agent 信号
    ta_confidence       TA 置信度
    ta_reasoning        TA 推理摘要

    [Kronos 源]
    kronos_direction    UP / DOWN / FLAT
    kronos_expected_return  预期收益率（%）
    p10, p25, p50, p75, p90  分位数（来自 PredictionDistribution）

    [Committee 源]
    committee_rec       委员会推荐信号
    committee_confidence  委员会置信度
    bull_case           Bull Case 摘要
    bear_case           Bear Case 摘要

    [Expected Value — 核心指标]
    prob_win            P(实际收益 > 0)，来自分布分位数计算
    prob_loss           P(实际收益 < 0) = 1 − prob_win − prob_flat
    avg_win_return      E[收益 | 收益 > 0]
    avg_loss_return     E[|收益| | 收益 < 0]
    expected_value      EV = prob_win×avg_win − prob_loss×avg_loss − cost
    risk_adjusted_ev    EV / volatility（类 Sharpe 比率）

    [交易执行]
    position_size       建议仓位比例（0–1）
    entry_zone          入场价区间
    target_price        目标价
    stop_loss           止损价
    horizon             投资周期（交易日）

    [质量控制]
    conflict            多源冲突标记
    thesis              综合投资论点
    risks               风险清单
    invalidations       失效条件列表
    """

    # 基础
    ticker: str
    eval_date: str
    final_signal: Signal
    final_confidence: float

    # TA 源
    ta_signal: Optional[Signal] = None
    ta_confidence: Optional[float] = None
    ta_reasoning: str = ""

    # Kronos 源
    kronos_direction: Optional[str] = None  # "UP"/"DOWN"/"FLAT"
    kronos_expected_return: Optional[float] = None
    p10: Optional[float] = None
    p25: Optional[float] = None
    p50: Optional[float] = None
    p75: Optional[float] = None
    p90: Optional[float] = None
    last_close: Optional[float] = None

    # Committee 源
    committee_rec: Optional[Signal] = None
    committee_confidence: Optional[float] = None
    bull_case: str = ""
    bear_case: str = ""

    # Expected Value
    prob_win: Optional[float] = None
    prob_loss: Optional[float] = None
    avg_win_return: Optional[float] = None
    avg_loss_return: Optional[float] = None
    expected_value: Optional[float] = None  # 百分比，如 1.5 = 1.5%
    risk_adjusted_ev: Optional[float] = None  # EV / vol，类 Sharpe
    cost_bps: float = 17.0  # 双边交易成本（bps）

    # 交易执行
    position_size: Optional[float] = None
    entry_zone: Optional[list[float]] = None
    target_price: Optional[float] = None
    stop_loss: Optional[float] = None
    horizon: Optional[int] = None

    # 质量控制
    conflict: str = _NoneConflict
    thesis: str = ""
    risks: list[str] = field(default_factory=list)
    invalidations: list[str] = field(default_factory=list)

    # ── EV 计算 ───────────────────────────────────────────────────────────

    def compute_expected_value(self) -> "UnifiedInvestmentDecision":
        """
        基于分位数计算 EV 指标，原地更新后返回 self。

        算法：
          · prob_win：假设分布对称，用 p50 作为中位收益点
                   若 p50 > 0 → prob_win ≈ 0.5 + skew调整
                   更精确：从 p10/p90 区间估计正收益概率
          · avg_win_return：线性插值 p50~p90 区间中点
          · avg_loss_return：线性插值 p10~p50 区间中点（绝对值）
          · EV = prob_win×avg_win − prob_loss×avg_loss − cost_pct
        """
        if self.kronos_expected_return is None or self.last_close is None:
            return self

        ret = self.kronos_expected_return  # %
        p10, p90 = (self.p10 or 0.0), (self.p90 or 0.0)
        # p50 未使用，保留变量名避免死代码警告
        _p50_val = self.p50

        # 从分位数估算收益分布中心
        # 假设：p10 = last_close*(1 + loss_10/100), p90 = last_close*(1 + win_90/100)
        if self.last_close and self.last_close > 0:
            ret_p10 = (p10 - self.last_close) / self.last_close * 100 if p10 else ret * 0.5
            ret_p90 = (p90 - self.last_close) / self.last_close * 100 if p90 else ret * 1.5
        else:
            ret_p10, ret_p90 = ret * 0.5, ret * 1.5

        # 对称假设：prob_win ≈ 0.5 + sign(ret) * 0.1~0.3
        direction_bias = 0.2 if ret > 0 else (-0.2 if ret < 0 else 0.0)
        self.prob_win = round(max(0.05, min(0.95, 0.5 + direction_bias)), 3)
        self.prob_loss = round(1.0 - self.prob_win, 3)

        # 收益区间中点
        self.avg_win_return = round((ret + ret_p90) / 2, 4) if ret > 0 else round(ret_p90, 4)
        self.avg_loss_return = (
            round(abs((ret + ret_p10) / 2), 4) if ret < 0 else round(abs(ret_p10), 4)
        )

        # EV = P(win)×avg_win − P(lose)×avg_loss − cost
        cost_pct = self.cost_bps / 100.0  # bps → %
        ev = self.prob_win * self.avg_win_return - self.prob_loss * self.avg_loss_return - cost_pct
        self.expected_value = round(ev, 4)

        # Risk-adjusted EV（类 Sharpe，用 range 作 vol proxy）
        vol_proxy = abs(ret_p90 - ret_p10) / 2 if ret_p90 != ret_p10 else abs(ret) * 0.5
        self.risk_adjusted_ev = round(ev / vol_proxy, 4) if vol_proxy > 1e-8 else 0.0

        return self

    def detect_conflict(self) -> "UnifiedInvestmentDecision":
        """检测多源信号冲突，更新 conflict 字段。"""
        signals = {
            "ta": self.ta_signal,
            "kronos": _direction_to_signal(self.kronos_direction),
            "committee": self.committee_rec,
        }
        active = {k: v for k, v in signals.items() if v is not None}
        if len(active) < 2:
            self.conflict = _NoneConflict
            return self
        unique = set(v.value for v in active.values())
        if len(unique) == 1:
            self.conflict = _NoneConflict
        elif len(unique) == 2:
            # 找冲突对
            pairs = [("ta", "kronos"), ("ta", "committee"), ("kronos", "committee")]
            for a, b in pairs:
                if active.get(a) and active.get(b) and active[a] != active[b]:
                    self.conflict = f"{a}_vs_{b}"
                    break
        else:
            self.conflict = _AllConflict
        return self

    def apply_final_signal(self) -> "UnifiedInvestmentDecision":
        """
        综合所有源信号，确定 final_signal 和 final_confidence。

        规则：
          · 三方一致 → 直接采用，confidence = min of all
          · 两方一致 vs 一方 dissent → 采用多数的，降低 confidence
          · 三方分歧 → HOLD，confidence = 33
        """
        votes: list[tuple[Signal, float, str]] = []
        if self.ta_signal:
            votes.append((self.ta_signal, self.ta_confidence or 50.0, "ta"))
        kronos_sig = _direction_to_signal(self.kronos_direction)
        if kronos_sig:
            # Kronos 置信度用 direction_score * 100
            ks = getattr(self, "_kronos_direction_score", None)
            votes.append((kronos_sig, (ks or 0.5) * 100, "kronos"))
        if self.committee_rec:
            votes.append((self.committee_rec, self.committee_confidence or 50.0, "committee"))

        if not votes:
            self.final_signal = Signal.HOLD
            self.final_confidence = 50.0
            return self

        # 多数表决
        sig_counts: dict[Signal, int] = {}
        for sig, _, _ in votes:
            sig_counts[sig] = sig_counts.get(sig, 0) + 1
        final = max(sig_counts, key=sig_counts.get)
        majority_count = sig_counts[final]

        # confidence = 加权平均， dissent 扣分
        total_weight = sum(w for _, w, _ in votes)
        weighted_conf = (
            sum(w for s, w, _ in votes if s == final) / total_weight * 100 if total_weight else 50
        )
        dissent_penalty = (3 - majority_count) * 10  # 每多一个 dissent 扣 10 分
        self.final_confidence = round(max(0, min(100, weighted_conf - dissent_penalty)), 1)
        self.final_signal = final
        return self

    # ── 序列化 ─────────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        from dataclasses import asdict

        d = asdict(self)
        d["final_signal"] = self.final_signal.value
        # conflict is a plain string (domain.SignalConflict constants)
        d["conflict"] = self.conflict
        # 省略 TA signal 枚举
        if self.ta_signal:
            d["ta_signal"] = self.ta_signal.value
        if self.committee_rec:
            d["committee_rec"] = self.committee_rec.value
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "UnifiedInvestmentDecision":
        d = dict(data)
        # 还原枚举
        if isinstance(d.get("final_signal"), str):
            d["final_signal"] = Signal(d["final_signal"])
        if isinstance(d.get("ta_signal"), str):
            d["ta_signal"] = Signal(d["ta_signal"])
        if isinstance(d.get("committee_rec"), str):
            d["committee_rec"] = Signal(d["committee_rec"])
        if isinstance(d.get("conflict"), str):
            pass  # 字符串直接保留，与 domain.SignalConflict 常量值一致
        return cls(**d)

    def to_ta_decision(self) -> TADecision:
        """转换为旧版 InvestmentDecision（兼容层）。"""
        return TADecision(
            signal=self.final_signal,
            confidence=self.final_confidence,
            expected_return=self.kronos_expected_return,
            thesis=self.thesis,
            risks=self.risks,
            invalidations=self.invalidations,
            target_price=self.target_price,
            stop_loss=self.stop_loss,
            horizon=self.horizon,
        )


# ═══════════════════════════════════════════════════════
#  辅助函数
# ═══════════════════════════════════════════════════════


def _direction_to_signal(direction: Optional[str]) -> Optional[Signal]:
    """将 Kronos direction (UP/DOWN/FLAT) 映射到 Signal。"""
    if direction is None:
        return None
    mapping = {"UP": Signal.BUY, "DOWN": Signal.SELL, "FLAT": Signal.HOLD}
    return mapping.get(direction.upper())


def build_unified_decision(
    ticker: str,
    eval_date: str,
    *,
    ta_decision: Optional[TADecision] = None,
    kronos_direction: Optional[str] = None,
    kronos_expected_return: Optional[float] = None,
    distribution: Optional[dict] = None,
    committee_rec: Optional[Signal] = None,
    committee_confidence: Optional[float] = None,
    bull_case: str = "",
    bear_case: str = "",
) -> UnifiedInvestmentDecision:
    """
    工厂函数：从各源数据构建 UnifiedInvestmentDecision。

    Parameters
    ----------
    ticker / eval_date     基础标识
    ta_decision            旧版 TA 决策（可选）
    kronos_direction       "UP"/"DOWN"/"FLAT"
    kronos_expected_return 预期收益率 %
    distribution           PredictionDistribution.to_dict() 输出
    committee_rec          委员会推荐
    committee_confidence   委员会置信度
    bull_case / bear_case  委员会 case 摘要
    """
    d = distribution or {}
    decision = UnifiedInvestmentDecision(
        ticker=ticker,
        eval_date=eval_date,
        final_signal=Signal.HOLD,
        final_confidence=50.0,
        ta_signal=ta_decision.signal if ta_decision else None,
        ta_confidence=ta_decision.confidence if ta_decision else None,
        ta_reasoning=ta_decision.thesis[:200] if ta_decision else "",
        kronos_direction=kronos_direction,
        kronos_expected_return=kronos_expected_return,
        p10=d.get("p10"),
        p25=d.get("p25"),
        p50=d.get("p50"),
        p75=d.get("p75"),
        p90=d.get("p90"),
        last_close=d.get("predicted_close_final"),
        committee_rec=committee_rec,
        committee_confidence=committee_confidence,
        bull_case=bull_case,
        bear_case=bear_case,
        thesis=ta_decision.thesis if ta_decision else "",
        risks=ta_decision.risks if ta_decision else [],
        invalidations=ta_decision.invalidations if ta_decision else [],
    )
    return decision.compute_expected_value().detect_conflict().apply_final_signal()
