"""SignalLifecycle — 信号生命周期管理器。

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

子模块：
  types.py  — SignalLifecycleState / SignalRecord / 迁移规则 / 工厂函数
"""

from __future__ import annotations

import time

from loguru import logger

from trade_krono_cli.signal_lifecycle.types import (
    _STATE_LABELS,
    SignalLifecycleState,
    SignalRecord,
    _determine_next_state,
    build_signal_record,
    next_state,
)

# ── 向后兼容导出（外部代码可直接 from trade_krono_cli.signal_lifecycle import ...）
__all__ = [
    "SignalLifecycle",
    "SignalLifecycleState",
    "SignalRecord",
    "_determine_next_state",
    "build_signal_record",
    "next_state",
]

# ═══════════════════════════════════════════════════════
#  SignalLifecycle 管理器
# ═══════════════════════════════════════════════════════


class SignalLifecycle:
    """信号生命周期管理器。

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
        """根据当前状态 + 新分析结果，计算生命周期状态并持久化。

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
            f"→ {new_state.value} | reason: {reason}",
        )
        return record

    def get_current(self, ticker: str) -> dict | None:
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
        state_filter: str | None = None,
    ) -> list[dict]:
        """查询某只股票的完整信号生命周期历史。

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
            (
                f"  当前状态 : {current['lifecycle_state']} "
                f"({_STATE_LABELS.get(SignalLifecycleState(current['lifecycle_state']), '')})"
            ),
            f"  最新日期 : {current['date']}",
            (
                f"  信号     : {current['signal']}  confidence={current['confidence']:.0f}  "
                f"score={current['composite_score']:.1f}"
            ),
            f"  迁移原因 : {current['transition_reason']}",
        ]
        if len(history) > 1:
            lines.append(f"  历史轨迹 ({len(history)} 条):")
            for rec in history[1:]:
                lines.append(
                    f"    {rec['date']}  {rec['signal']} "
                    f"conf={rec['confidence']:.0f} "
                    f"→ {rec['lifecycle_state']} | {rec['transition_reason']}",
                )
        return "\n".join(lines)

    # ── 持久化 ─────────────────────────────────────────────────────────────

    def _persist(self, record: SignalRecord) -> None:
        """将信号记录写入研究数据库。"""
        with self._db._conn as conn:
            conn.execute(
                """
                INSERT INTO signal_history
                    (ticker, date, signal, confidence, composite_score,
                     lifecycle_state, previous_state, transition_reason,
                     job_id, run_id, thesis_snapshot, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
