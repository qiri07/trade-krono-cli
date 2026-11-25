"""trade_krono_cli.t1_tracker 单元测试。

覆盖 T1Tracker 记录买入、T+1 卖出约束、locked_until 与 clear。
"""

from __future__ import annotations

from trade_krono_cli.t1_tracker import T1Tracker


class TestT1Tracker:
    """T1Tracker 核心行为测试。"""

    def test_can_sell_no_buy_record(self) -> None:
        tracker = T1Tracker()
        # 从未买入，可自由卖出
        assert tracker.can_sell("sh.600519", "2026-08-11") is True

    def test_can_sell_same_day_blocked(self) -> None:
        tracker = T1Tracker()
        tracker.record_buy("sh.600519", "2026-08-11")
        # 买入当日不可卖出
        assert tracker.can_sell("sh.600519", "2026-08-11") is False

    def test_can_sell_next_day_allowed(self) -> None:
        tracker = T1Tracker()
        tracker.record_buy("sh.600519", "2026-08-11")
        # 次日及之后可卖出
        assert tracker.can_sell("sh.600519", "2026-08-12") is True
        assert tracker.can_sell("sh.600519", "2026-08-13") is True

    def test_can_sell_before_buy_blocked(self) -> None:
        tracker = T1Tracker()
        tracker.record_buy("sh.600519", "2026-08-11")
        assert tracker.can_sell("sh.600519", "2026-08-10") is False

    def test_tickers_independent(self) -> None:
        tracker = T1Tracker()
        tracker.record_buy("sh.600519", "2026-08-11")
        # 不同股票互不影响
        assert tracker.can_sell("sz.000858", "2026-08-11") is True
        assert tracker.can_sell("sh.600519", "2026-08-11") is False

    def test_record_buy_overwrites(self) -> None:
        tracker = T1Tracker()
        tracker.record_buy("sh.600519", "2026-08-11")
        assert tracker.can_sell("sh.600519", "2026-08-12") is True
        tracker.record_buy("sh.600519", "2026-08-12")
        # 更新买入日期后，2026-08-12 当日再不可卖
        assert tracker.can_sell("sh.600519", "2026-08-12") is False

    def test_locked_until(self) -> None:
        tracker = T1Tracker()
        assert tracker.locked_until("sh.600519") is None
        tracker.record_buy("sh.600519", "2026-08-11")
        from datetime import date

        assert tracker.locked_until("sh.600519") == date(2026, 8, 12)

    def test_locked_until_invalid_date(self) -> None:
        tracker = T1Tracker()
        tracker.record_buy("sh.600519", "not-a-date")
        assert tracker.locked_until("sh.600519") is None

    def test_clear(self) -> None:
        tracker = T1Tracker()
        tracker.record_buy("sh.600519", "2026-08-11")
        assert tracker.can_sell("sh.600519", "2026-08-11") is False
        tracker.clear()
        assert tracker.can_sell("sh.600519", "2026-08-11") is True
