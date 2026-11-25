"""trade_krono_cli.trading_constraints.t1 单元测试。

覆盖 enforce_t1：T+1 锁定、允许卖出、禁用 T+1、缺省配置。
使用真实 T1Tracker（已有 test_t1_tracker 覆盖其自身行为）。
"""

from __future__ import annotations

from trade_krono_cli.constraints_config import ConstraintConfig
from trade_krono_cli.t1_tracker import T1Tracker
from trade_krono_cli.trading_constraints.t1 import enforce_t1


class TestEnforceT1:
    def test_allow_when_no_buy_record(self) -> None:
        tracker = T1Tracker()
        res = enforce_t1("sh.600519", "2026-08-12", tracker)
        assert res.symbol == "sh.600519"
        assert res.allowed is True
        assert res.reason is None

    def test_blocked_t_plus_one_same_day(self) -> None:
        tracker = T1Tracker()
        tracker.record_buy("sh.600519", "2026-08-11")
        res = enforce_t1("sh.600519", "2026-08-11", tracker)
        assert res.allowed is False
        assert res.reason == "T1_LOCKED(until=2026-08-12)"
        assert res.position_locked_until is not None

    def test_allowed_next_day(self) -> None:
        tracker = T1Tracker()
        tracker.record_buy("sh.600519", "2026-08-11")
        res = enforce_t1("sh.600519", "2026-08-12", tracker)
        assert res.allowed is True
        assert res.reason is None

    def test_t1_disabled_allows(self) -> None:
        tracker = T1Tracker()
        tracker.record_buy("sh.600519", "2026-08-11")
        config = ConstraintConfig(enable_t1=False)
        res = enforce_t1("sh.600519", "2026-08-11", tracker, config=config)
        assert res.allowed is True
        assert res.reason is None

    def test_default_config_enable_t1_true(self) -> None:
        tracker = T1Tracker()
        tracker.record_buy("sh.600519", "2026-08-11")
        res = enforce_t1("sh.600519", "2026-08-11", tracker)
        # 缺省 config enable_t1=True，应被锁定
        assert res.allowed is False

    def test_config_none_uses_default(self) -> None:
        tracker = T1Tracker()
        tracker.record_buy("sz.000858", "2026-08-11")
        res = enforce_t1("sz.000858", "2026-08-11", tracker, config=None)
        assert res.allowed is False
        assert res.reason == "T1_LOCKED(until=2026-08-12)"
