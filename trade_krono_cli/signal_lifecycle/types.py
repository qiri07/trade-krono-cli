"""信号生命周期类型定义与状态迁移规则。

包含：
  · SignalLifecycleState — 生命周期状态枚举
  · SignalRecord         — 信号快照数据类
  · _determine_next_state — 状态迁移核心规则
  · build_signal_record   — 工厂函数
  · next_state            — 便捷包装
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum

# ═══════════════════════════════════════════════════════
#  生命周期状态枚举
# ═══════════════════════════════════════════════════════


class SignalLifecycleState(str, Enum):
    """信号生命周期状态。

    CREATED   — 首次出现 BUY/HOLD 信号，投资逻辑建立
    ACTIVE    — 信号持续有效，置信度维持在合理水平
    UPDATED   — 信号方向未变，但置信度/基本面有所更新
    WEAKENED  — 信号仍在，但置信度明显下降
    INVALIDATED — 信号方向反转（BUY→HOLD/SELL），原逻辑失效
    CLOSED    — 终态，不再追踪（手动关闭或明确 SELL）
    """

    CREATED = "CREATED"
    ACTIVE = "ACTIVE"
    UPDATED = "UPDATED"
    WEAKENED = "WEAKENED"
    INVALIDATED = "INVALIDATED"
    CLOSED = "CLOSED"


# 信号状态 ↔ 人类可读描述
_STATE_LABELS: dict[SignalLifecycleState, str] = {
    SignalLifecycleState.CREATED: "新建",
    SignalLifecycleState.ACTIVE: "活跃",
    SignalLifecycleState.UPDATED: "更新",
    SignalLifecycleState.WEAKENED: "弱化",
    SignalLifecycleState.INVALIDATED: "失效",
    SignalLifecycleState.CLOSED: "已关闭",
}

# ═══════════════════════════════════════════════════════
#  数据类
# ═══════════════════════════════════════════════════════


@dataclass(frozen=True)
class SignalRecord:
    """信号生命周期的一个快照点。

    Attributes
    ----------
    ticker              : 股票代码（如 "sh.600519"）
    date                : 分析日期
    signal              : 本次分析的信号（BUY/HOLD/SELL）
    confidence          : 信号置信度（0-100）
    composite_score     : 综合评分（0-100，来自合并打分）
    lifecycle_state     : 当前生命周期状态
    previous_state      : 前一个生命周期状态（None 表示首次）
    transition_reason   : 状态迁移原因描述
    job_id              : 关联的研究作业 ID
    run_id              : 关联的运行 ID
    thesis_snapshot     : 本次投资论点摘要（截断）

    """

    ticker: str
    date: str
    signal: str  # "BUY" / "HOLD" / "SELL"
    confidence: float
    composite_score: float
    lifecycle_state: SignalLifecycleState
    previous_state: str | None = None
    transition_reason: str = ""
    job_id: str = ""
    run_id: str = ""
    thesis_snapshot: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["lifecycle_state"] = self.lifecycle_state.value
        return d

    @classmethod
    def from_dict(cls, data: dict) -> SignalRecord:
        state = data.get("lifecycle_state", "ACTIVE")
        if isinstance(state, str):
            state = SignalLifecycleState(state)
        data = dict(data)
        data["lifecycle_state"] = state
        return cls(**data)


# ═══════════════════════════════════════════════════════
#  状态迁移规则
# ═══════════════════════════════════════════════════════


def _determine_next_state(
    current_state: SignalLifecycleState | None,
    current_confidence: float,
    new_signal: str,
    new_confidence: float,
    min_active_confidence: float = 55.0,
    weak_threshold: float = 60.0,
) -> tuple[SignalLifecycleState, str]:
    """根据当前状态 + 新分析结果，推断下一个生命周期状态。

    规则：
      current = None (首次)
        → new_signal == BUY  → CREATED
        → new_signal == HOLD → ACTIVE（观望即活跃）
        → new_signal == SELL → CLOSED

      current = CREATED / ACTIVE / UPDATED
        → new_signal == SELL           → INVALIDATED（明确卖出）
        → new_signal == HOLD           → INVALIDATED（原逻辑失效）
        → new_signal == BUY and conf >= weak_threshold → UPDATED
        → new_signal == BUY and conf < weak_threshold  → WEAKENED
        → new_signal == BUY and conf >= min_active     → ACTIVE

      current = WEAKENED
        → new_signal == SELL/HOLD    → INVALIDATED
        → new_signal == BUY and conf >= weak_threshold → UPDATED（恢复）
        → new_signal == BUY and conf < weak_threshold  → WEAKENED（持续弱化）

      current = INVALIDATED / CLOSED
        → 终态，不再变化（记录 CREATED 并说明重建原因）
    """
    if current_state in (SignalLifecycleState.INVALIDATED, SignalLifecycleState.CLOSED):
        if new_signal == "BUY" and new_confidence >= min_active_confidence:
            return (
                SignalLifecycleState.CREATED,
                f"信号重建：{current_state.value} → BUY(conf={new_confidence:.0f})",
            )
        return (current_state, "终态保持不变")

    if current_state is None:
        # 首次出现
        if new_signal == "BUY":
            return SignalLifecycleState.CREATED, "首次买入信号"
        if new_signal == "HOLD":
            return SignalLifecycleState.ACTIVE, "首次观望信号（保持关注）"
        return SignalLifecycleState.CLOSED, "首次出现卖出信号"

    # 已有历史记录
    if new_signal == "SELL":
        return (
            SignalLifecycleState.INVALIDATED,
            f"信号反转：{new_signal}（原 {current_state.value} 逻辑失效）",
        )

    if new_signal == "HOLD" and current_state != SignalLifecycleState.CLOSED:
        return (
            SignalLifecycleState.INVALIDATED,
            f"信号弱化：BUY→HOLD（{current_state.value} → INVALIDATED）",
        )

    if new_signal == "BUY":
        if new_confidence >= weak_threshold:
            if current_state == SignalLifecycleState.WEAKENED:
                return (
                    SignalLifecycleState.UPDATED,
                    f"信心恢复：{current_state.value} → UPDATED（conf={new_confidence:.0f}）",
                )
            return (
                SignalLifecycleState.UPDATED,
                f"信号更新：{current_state.value} → UPDATED（conf={new_confidence:.0f}）",
            )
        # confidence < weak_threshold
        if current_state == SignalLifecycleState.WEAKENED:
            return (
                SignalLifecycleState.WEAKENED,
                f"持续弱化：conf={new_confidence:.0f} < {weak_threshold}",
            )
        return (
            SignalLifecycleState.WEAKENED,
            f"信心下降：{current_state.value} → WEAKENED（conf={new_confidence:.0f} < {weak_threshold}）",
        )

    # HOLD 信号，但当前是 BUY 逻辑活跃中
    if new_signal == "HOLD":
        return (
            SignalLifecycleState.INVALIDATED,
            f"信号失效：BUY→HOLD（{current_state.value} → INVALIDATED）",
        )

    return (current_state, "无变化")


# ═══════════════════════════════════════════════════════
#  工厂函数
# ═══════════════════════════════════════════════════════


def build_signal_record(
    ticker: str,
    date: str,
    signal: str,
    confidence: float,
    composite_score: float,
    lifecycle_state: SignalLifecycleState,
    previous_state: str | None = None,
    transition_reason: str = "",
    job_id: str = "",
    run_id: str = "",
    thesis: str = "",
) -> SignalRecord:
    """工厂函数：创建 SignalRecord。"""
    return SignalRecord(
        ticker=ticker,
        date=date,
        signal=signal,
        confidence=round(confidence, 1),
        composite_score=round(composite_score, 1),
        lifecycle_state=lifecycle_state,
        previous_state=previous_state,
        transition_reason=transition_reason,
        job_id=job_id,
        run_id=run_id,
        thesis_snapshot=thesis[:200] if thesis else "",
    )


def next_state(
    current_state: SignalLifecycleState | None,
    current_confidence: float,
    new_signal: str,
    new_confidence: float,
    min_active_confidence: float = 55.0,
    weak_threshold: float = 60.0,
) -> tuple[SignalLifecycleState, str]:
    """便捷函数：计算下一个生命周期状态和迁移原因。"""
    return _determine_next_state(
        current_state,
        current_confidence,
        new_signal,
        new_confidence,
        min_active_confidence,
        weak_threshold,
    )
