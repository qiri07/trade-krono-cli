"""
SignalLifecycle — 信号生命周期管理器。

每个股票的分析结果不是孤立的瞬态，而是一条随时间演化的状态链：

    CREATED → ACTIVE → UPDATED / WEAKENED → INVALIDATED → CLOSED

核心能力：
  · 基于历史信号状态 + 本次分析结果，自动推断当前生命周期状态
  · 状态迁移规则明确，支持审计和历史回溯
  · 每次状态变更持久化到研究数据库（signal_history 表）
  · 提供 signal_history(ticker) 查询任意股票的全生命周期轨迹

设计原则：
  · 信号是"对未来的判断"，不是"当下的结论"——必须有时间维度
  · 同一个 ticker 在不同日期出现，代表同一笔投资逻辑的持续追踪
  · INVALIDATED 是可恢复的（重新 BUY 时回到 CREATED）
  · CLOSED 是终态，不再变化
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Optional

from loguru import logger

# ═══════════════════════════════════════════════════════
#  生命周期状态枚举
# ═══════════════════════════════════════════════════════


class SignalLifecycleState(str, Enum):
    """
    信号生命周期状态。

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
    """
    信号生命周期的一个快照点。

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
    previous_state: Optional[str] = None
    transition_reason: str = ""
    job_id: str = ""
    run_id: str = ""
    thesis_snapshot: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["lifecycle_state"] = self.lifecycle_state.value
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "SignalRecord":
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
    current_state: Optional[SignalLifecycleState],
    current_confidence: float,
    new_signal: str,
    new_confidence: float,
    min_active_confidence: float = 55.0,
    weak_threshold: float = 60.0,
) -> tuple[SignalLifecycleState, str]:
    """
    根据当前状态 + 新分析结果，推断下一个生命周期状态。

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
#  SignalLifecycle 管理器
# ═══════════════════════════════════════════════════════


class SignalLifecycle:
    """
    信号生命周期管理器。

    用法：
        lifecycle = SignalLifecycle(research_db)

        # 分析完成后更新单只股票的信号历史
        record = lifecycle.update(
            ticker="sh.600519",
            date="2026-08-14",
            signal="BUY",
            confidence=82.0,
            composite_score=75.5,
            job_id="abc123",
            run_id="20260814-120000-001",
            thesis="基本面强劲，估值合理",
        )

        # 查询某股票的全生命周期
        history = lifecycle.get_history("sh.600519", limit=10)

        # 获取当前状态
        current = lifecycle.get_current("sh.600519")
    """

    def __init__(self, research_db) -> None:
        self._db = research_db

    # ── 核心 API ───────────────────────────────────────────────────────────

    def update(
        self,
        ticker: str,
        date: str,
        signal: str,
        confidence: float,
        composite_score: float,
        job_id: str,
        run_id: str = "",
        thesis: str = "",
        min_active_confidence: float = 55.0,
        weak_threshold: float = 60.0,
    ) -> SignalRecord:
        """
        根据当前状态 + 新分析结果，计算生命周期状态并持久化。

        Parameters
        ----------
        ticker                : 股票代码
        date                  : 分析日期
        signal                : 本次分析信号（BUY/HOLD/SELL）
        confidence            : 信号置信度（0-100）
        composite_score       : 综合评分（0-100）
        job_id                : 研究作业 ID
        run_id                : 运行 ID
        thesis                : 投资论点摘要
        min_active_confidence : 判定为 ACTIVE 的最低置信度
        weak_threshold        : 低于此值判定为 WEAKENED

        Returns
        -------
        SignalRecord : 本次更新的记录
        """
        current = self.get_current(ticker)
        prev_state = None
        if current:
            ls = current["lifecycle_state"]
            prev_state = SignalLifecycleState(ls) if isinstance(ls, str) else ls
        prev_confidence = current["confidence"] if current else None

        new_state, reason = _determine_next_state(
            current_state=prev_state,
            current_confidence=prev_confidence or 0.0,
            new_signal=signal,
            new_confidence=confidence,
            min_active_confidence=min_active_confidence,
            weak_threshold=weak_threshold,
        )

        record = SignalRecord(
            ticker=ticker,
            date=date,
            signal=signal,
            confidence=round(confidence, 1),
            composite_score=round(composite_score, 1),
            lifecycle_state=new_state,
            previous_state=prev_state.value if prev_state else None,
            transition_reason=reason,
            job_id=job_id,
            run_id=run_id,
            thesis_snapshot=thesis[:200] if thesis else "",
        )

        self._persist(record)
        logger.info(
            f"📡 信号生命周期 [{ticker}] {prev_state.value if prev_state else '—'} "
            f"→ {new_state.value} | reason: {reason}"
        )
        return record

    def get_current(self, ticker: str) -> Optional[dict]:
        """获取某只股票的最新信号记录（委托给 ResearchDatabase）。"""
        raw = self._db.get_latest_signal_for_ticker(ticker)
        if not raw:
            return None
        return {
            "ticker": raw["ticker"],
            "date": raw["date"],
            "signal": raw["signal"],
            "confidence": raw["confidence"],
            "composite_score": raw["composite_score"],
            "lifecycle_state": raw["lifecycle_state"],
            "previous_state": raw.get("previous_state"),
            "transition_reason": raw.get("transition_reason", ""),
            "job_id": raw.get("job_id", ""),
            "run_id": raw.get("run_id", ""),
            "thesis_snapshot": "",
        }

    def get_history(
        self,
        ticker: str,
        limit: int = 20,
        state_filter: Optional[str] = None,
    ) -> list[dict]:
        """
        查询某只股票的完整信号生命周期历史。

        Parameters
        ----------
        ticker       : 股票代码
        limit        : 最多返回条数
        state_filter : 可选，按 lifecycle_state 筛选（如 "INVALIDATED"）

        Returns
        -------
        list[dict] : 按 date 降序排列
        """
        sql = """
            SELECT ticker, date, signal, confidence, composite_score,
                   lifecycle_state, previous_state, transition_reason,
                   job_id, run_id, thesis_snapshot
            FROM signal_history
            WHERE ticker = ?
        """
        params: list = [ticker]
        if state_filter:
            sql += " AND lifecycle_state = ?"
            params.append(state_filter)
        sql += " ORDER BY date DESC LIMIT ?"
        params.append(limit)

        with self._db._conn as conn:
            rows = conn.execute(sql, params).fetchall()
        return [
            {
                "ticker": r[0],
                "date": r[1],
                "signal": r[2],
                "confidence": r[3],
                "composite_score": r[4],
                "lifecycle_state": r[5],
                "previous_state": r[6],
                "transition_reason": r[7],
                "job_id": r[8],
                "run_id": r[9],
                "thesis_snapshot": r[10],
            }
            for r in rows
        ]

    def describe(self, ticker: str) -> str:
        """返回某只股票信号生命周期的可读描述。"""
        current = self.get_current(ticker)
        if not current:
            return f"[{ticker}] 暂无信号历史"

        history = self.get_history(ticker, limit=10)
        lines = [
            f"📡 {ticker} 信号生命周期",
            f"  当前状态 : {current['lifecycle_state']} "
            f"({_STATE_LABELS.get(SignalLifecycleState(current['lifecycle_state']), '')})",
            f"  最新日期 : {current['date']}",
            f"  信号     : {current['signal']}  confidence={current['confidence']:.0f}  "
            f"score={current['composite_score']:.1f}",
            f"  迁移原因 : {current['transition_reason']}",
        ]
        if len(history) > 1:
            lines.append(f"  历史轨迹 ({len(history)} 条):")
            for rec in history[1:]:
                lines.append(
                    f"    {rec['date']}  {rec['signal']} "
                    f"conf={rec['confidence']:.0f} "
                    f"→ {rec['lifecycle_state']} | {rec['transition_reason']}"
                )
        return "\n".join(lines)

    # ── 持久化 ─────────────────────────────────────────────────────────────

    def _persist(self, record: SignalRecord) -> None:
        """将 SignalRecord 写入研究数据库。"""
        with self._db._conn as conn:
            conn.execute(
                """
                INSERT INTO signal_history
                    (ticker, date, signal, confidence, composite_score,
                     lifecycle_state, previous_state, transition_reason,
                     job_id, run_id, thesis_snapshot, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    record.ticker,
                    record.date,
                    record.signal,
                    record.confidence,
                    record.composite_score,
                    record.lifecycle_state.value,
                    record.previous_state,
                    record.transition_reason,
                    record.job_id,
                    record.run_id,
                    record.thesis_snapshot,
                    time.time(),
                ),
            )
            conn.commit()


# ═══════════════════════════════════════════════════════
#  模块级便捷函数
# ═══════════════════════════════════════════════════════


def build_signal_record(
    ticker: str,
    date: str,
    signal: str,
    confidence: float,
    composite_score: float,
    lifecycle_state: SignalLifecycleState,
    previous_state: Optional[str] = None,
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
    current_state: Optional[SignalLifecycleState],
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
