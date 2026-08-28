"""测试 SignalLifecycle — 信号生命周期管理器。"""

import pytest

from trade_krono_cli.research_db import ResearchDatabase
from trade_krono_cli.signal_lifecycle import (
    SignalLifecycle,
    SignalLifecycleState,
    SignalRecord,
    _determine_next_state,
    build_signal_record,
    next_state,
)

# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def research_db(tmp_path):
    db = tmp_path / "research.db"
    return ResearchDatabase(db_path=db)


@pytest.fixture
def lifecycle(research_db):
    return SignalLifecycle(research_db)


# ── State Transition Rules ───────────────────────────────────────────────────


class TestDetermineNextState:
    """测试状态迁移规则。"""

    def test_first_buy_creates(self):
        state, reason = _determine_next_state(None, 0.0, "BUY", 80.0)
        assert state == SignalLifecycleState.CREATED
        assert "首次" in reason

    def test_first_hold_becomes_active(self):
        state, reason = _determine_next_state(None, 0.0, "HOLD", 60.0)
        assert state == SignalLifecycleState.ACTIVE

    def test_first_sell_becomes_closed(self):
        state, reason = _determine_next_state(None, 0.0, "SELL", 90.0)
        assert state == SignalLifecycleState.CLOSED

    def test_buy_high_conf_from_active_becomes_updated(self):
        state, reason = _determine_next_state(SignalLifecycleState.ACTIVE, 70.0, "BUY", 82.0)
        assert state == SignalLifecycleState.UPDATED

    def test_buy_low_conf_from_active_becomes_weakened(self):
        state, reason = _determine_next_state(SignalLifecycleState.ACTIVE, 80.0, "BUY", 50.0)
        assert state == SignalLifecycleState.WEAKENED

    def test_buy_recover_from_weakened(self):
        state, reason = _determine_next_state(SignalLifecycleState.WEAKENED, 40.0, "BUY", 75.0)
        assert state == SignalLifecycleState.UPDATED
        assert "恢复" in reason

    def test_hold_invalidates_buy(self):
        state, reason = _determine_next_state(SignalLifecycleState.ACTIVE, 80.0, "HOLD", 60.0)
        assert state == SignalLifecycleState.INVALIDATED

    def test_sell_invalidates_any_active(self):
        for s in (
            SignalLifecycleState.ACTIVE,
            SignalLifecycleState.UPDATED,
            SignalLifecycleState.WEAKENED,
            SignalLifecycleState.CREATED,
        ):
            state, _ = _determine_next_state(s, 70.0, "SELL", 50.0)
            assert state == SignalLifecycleState.INVALIDATED

    def test_invalidated_stays_invalidated_on_hold(self):
        state, reason = _determine_next_state(SignalLifecycleState.INVALIDATED, 30.0, "HOLD", 55.0)
        assert state == SignalLifecycleState.INVALIDATED
        assert "终态" in reason

    def test_closed_stays_closed_on_sell(self):
        state, reason = _determine_next_state(SignalLifecycleState.CLOSED, 20.0, "SELL", 90.0)
        assert state == SignalLifecycleState.CLOSED

    def test_reactivated_after_invalidated_with_strong_buy(self):
        state, reason = _determine_next_state(SignalLifecycleState.INVALIDATED, 30.0, "BUY", 80.0)
        assert state == SignalLifecycleState.CREATED
        assert "重建" in reason

    def test_reactivated_after_closed_with_strong_buy(self):
        state, reason = _determine_next_state(SignalLifecycleState.CLOSED, 10.0, "BUY", 85.0)
        assert state == SignalLifecycleState.CREATED

    def test_weakened_continues_weakened_when_low(self):
        state, reason = _determine_next_state(SignalLifecycleState.WEAKENED, 45.0, "BUY", 40.0)
        assert state == SignalLifecycleState.WEAKENED


# ── SignalLifecycle Manager ──────────────────────────────────────────────────


class TestSignalLifecycle:
    """测试 SignalLifecycle 完整生命周期流程。"""

    def test_create_first_signal(self, lifecycle):
        record = lifecycle.update(
            ticker="sh.600519",
            date="2026-08-01",
            signal="BUY",
            confidence=80.0,
            composite_score=75.0,
            job_id="job-001",
            run_id="run-001",
            thesis="基本面强劲",
        )
        assert record.lifecycle_state == SignalLifecycleState.CREATED
        assert record.ticker == "sh.600519"
        assert record.signal == "BUY"
        assert record.confidence == 80.0

    def test_update_high_conf_promotes_to_updated(self, lifecycle):
        lifecycle.update(
            ticker="sh.600519",
            date="2026-08-01",
            signal="BUY",
            confidence=80.0,
            composite_score=75.0,
            job_id="job-001",
            thesis="建仓",
        )
        record = lifecycle.update(
            ticker="sh.600519",
            date="2026-08-05",
            signal="BUY",
            confidence=82.0,
            composite_score=78.0,
            job_id="job-002",
            thesis="信心增强",
        )
        assert record.lifecycle_state == SignalLifecycleState.UPDATED

    def test_hold_invalidates_buy(self, lifecycle):
        lifecycle.update(
            ticker="sh.600519",
            date="2026-08-01",
            signal="BUY",
            confidence=80.0,
            composite_score=75.0,
            job_id="job-001",
            thesis="建仓",
        )
        record = lifecycle.update(
            ticker="sh.600519",
            date="2026-08-10",
            signal="HOLD",
            confidence=61.0,
            composite_score=55.0,
            job_id="job-003",
            thesis="观望",
        )
        assert record.lifecycle_state == SignalLifecycleState.INVALIDATED
        assert "BUY→HOLD" in record.transition_reason

    def test_sell_invalidates_buy(self, lifecycle):
        lifecycle.update(
            ticker="sh.600519",
            date="2026-08-01",
            signal="BUY",
            confidence=80.0,
            composite_score=75.0,
            job_id="job-001",
            thesis="建仓",
        )
        record = lifecycle.update(
            ticker="sh.600519",
            date="2026-08-15",
            signal="SELL",
            confidence=90.0,
            composite_score=92.0,
            job_id="job-004",
            thesis="获利了结",
        )
        assert record.lifecycle_state == SignalLifecycleState.INVALIDATED

    def test_low_confidence_becomes_weakened(self, lifecycle):
        lifecycle.update(
            ticker="sh.600519",
            date="2026-08-01",
            signal="BUY",
            confidence=80.0,
            composite_score=75.0,
            job_id="job-001",
            thesis="建仓",
        )
        record = lifecycle.update(
            ticker="sh.600519",
            date="2026-08-08",
            signal="BUY",
            confidence=45.0,
            composite_score=40.0,
            job_id="job-002",
            thesis="信心下滑",
        )
        assert record.lifecycle_state == SignalLifecycleState.WEAKENED

    def test_get_current_returns_none_for_new_ticker(self, lifecycle):
        assert lifecycle.get_current("sh.999999") is None

    def test_get_current_returns_latest_record(self, lifecycle):
        lifecycle.update(
            ticker="sh.600519",
            date="2026-08-01",
            signal="BUY",
            confidence=80.0,
            composite_score=75.0,
            job_id="job-001",
            thesis="建仓",
        )
        current = lifecycle.get_current("sh.600519")
        assert current is not None
        assert current["signal"] == "BUY"
        assert current["confidence"] == 80.0
        assert current["lifecycle_state"] == "CREATED"

    def test_get_history_returns_all_records(self, lifecycle):
        for i, (date, sig, conf) in enumerate(
            [
                ("2026-08-01", "BUY", 80.0),
                ("2026-08-05", "BUY", 82.0),
                ("2026-08-10", "HOLD", 61.0),
            ]
        ):
            lifecycle.update(
                ticker="sh.600519",
                date=date,
                signal=sig,
                confidence=conf,
                composite_score=conf - 5.0,
                job_id=f"job-00{i}",
                thesis=f"论{i}",
            )
        history = lifecycle.get_history("sh.600519")
        assert len(history) == 3
        # 按日期降序
        assert history[0]["date"] == "2026-08-10"
        assert history[1]["date"] == "2026-08-05"
        assert history[2]["date"] == "2026-08-01"

    def test_get_history_with_state_filter(self, lifecycle):
        lifecycle.update(
            ticker="sh.600519",
            date="2026-08-01",
            signal="BUY",
            confidence=80.0,
            composite_score=75.0,
            job_id="job-001",
            thesis="建仓",
        )
        lifecycle.update(
            ticker="sh.600519",
            date="2026-08-10",
            signal="HOLD",
            confidence=61.0,
            composite_score=55.0,
            job_id="job-002",
            thesis="观望",
        )
        invalidated = lifecycle.get_history("sh.600519", state_filter="INVALIDATED")
        assert len(invalidated) == 1
        assert invalidated[0]["lifecycle_state"] == "INVALIDATED"

    def test_describe_returns_readble_output(self, lifecycle):
        lifecycle.update(
            ticker="sh.600519",
            date="2026-08-01",
            signal="BUY",
            confidence=80.0,
            composite_score=75.0,
            job_id="job-001",
            thesis="基本面强劲，估值合理",
        )
        desc = lifecycle.describe("sh.600519")
        assert "sh.600519" in desc
        assert "CREATED" in desc

    def test_describe_no_history(self, lifecycle):
        desc = lifecycle.describe("sh.999999")
        assert "暂无信号历史" in desc

    def test_reactivated_after_invalidated(self, lifecycle):
        # Phase 1: create
        lifecycle.update(
            ticker="sz.000858",
            date="2026-08-01",
            signal="BUY",
            confidence=80.0,
            composite_score=75.0,
            job_id="job-001",
            thesis="建仓",
        )
        # Phase 2: invalidated by sell
        lifecycle.update(
            ticker="sz.000858",
            date="2026-08-10",
            signal="SELL",
            confidence=90.0,
            composite_score=88.0,
            job_id="job-002",
            thesis="止损",
        )
        # Phase 3: reactivated with new strong BUY
        record = lifecycle.update(
            ticker="sz.000858",
            date="2026-08-20",
            signal="BUY",
            confidence=85.0,
            composite_score=82.0,
            job_id="job-003",
            thesis="重新建仓",
        )
        assert record.lifecycle_state == SignalLifecycleState.CREATED
        assert record.previous_state == "INVALIDATED"

    def test_persistence_across_instances(self, tmp_path):
        """同一 DB 的两个生命周期实例应共享数据。"""
        db = ResearchDatabase(db_path=tmp_path / "sig.db")
        lc1 = SignalLifecycle(db)
        lc2 = SignalLifecycle(db)

        lc1.update(
            ticker="sh.600519",
            date="2026-08-01",
            signal="BUY",
            confidence=80.0,
            composite_score=75.0,
            job_id="j1",
            thesis="test",
        )
        # 新实例应能读到旧实例写入的数据
        current = lc2.get_current("sh.600519")
        assert current is not None
        assert current["signal"] == "BUY"


# ── SignalRecord ─────────────────────────────────────────────────────────────


class TestSignalRecord:
    """测试 SignalRecord 数据类。"""

    def test_to_dict_and_from_dict_roundtrip(self):
        rec = build_signal_record(
            ticker="sh.600519",
            date="2026-08-01",
            signal="BUY",
            confidence=80.0,
            composite_score=75.0,
            lifecycle_state=SignalLifecycleState.CREATED,
            previous_state=None,
            transition_reason="首次",
            job_id="j1",
            run_id="r1",
            thesis="论点摘要",
        )
        d = rec.to_dict()
        assert d["lifecycle_state"] == "CREATED"
        restored = SignalRecord.from_dict(d)
        assert restored.ticker == rec.ticker
        assert restored.lifecycle_state == SignalLifecycleState.CREATED
        assert restored.signal == "BUY"

    def test_thesis_snapshot_truncated(self):
        long_thesis = "x" * 500
        rec = build_signal_record(
            ticker="sh.600519",
            date="2026-08-01",
            signal="BUY",
            confidence=80.0,
            composite_score=75.0,
            lifecycle_state=SignalLifecycleState.CREATED,
            thesis=long_thesis,
        )
        assert len(rec.thesis_snapshot) == 200


# ── Module-Level Convenience ────────────────────────────────────────────────


class TestModuleFunctions:
    """测试模块级便捷函数。"""

    def test_next_state_convenience(self):
        state, reason = next_state(None, 0.0, "BUY", 80.0)
        assert state == SignalLifecycleState.CREATED

    def test_build_signal_record_factory(self):
        rec = build_signal_record(
            ticker="sh.600519",
            date="2026-08-01",
            signal="BUY",
            confidence=80.0,
            composite_score=75.0,
            lifecycle_state=SignalLifecycleState.ACTIVE,
        )
        assert rec.ticker == "sh.600519"
        assert rec.lifecycle_state == SignalLifecycleState.ACTIVE


# ── DB Integration ───────────────────────────────────────────────────────────


class TestResearchDbSignalHistory:
    """测试 research_db signal_history 表的 CRUD。"""

    def test_get_latest_signal_for_ticker_empty(self, research_db):
        result = research_db.get_latest_signal_for_ticker("sh.999999")
        assert result is None

    def test_get_latest_signal_for_ticker_after_insert(self, lifecycle):
        lifecycle.update(
            ticker="sh.600519",
            date="2026-08-01",
            signal="BUY",
            confidence=80.0,
            composite_score=75.0,
            job_id="j1",
            run_id="r1",
            thesis="test",
        )
        result = lifecycle._db.get_latest_signal_for_ticker("sh.600519")
        assert result is not None
        assert result["signal"] == "BUY"
        assert result["lifecycle_state"] == "CREATED"

    def test_stats_includes_signal_history(self, research_db):
        stats = research_db.stats()
        assert "research_signal_history" in stats
        assert stats["research_signal_history"] == 0
