"""A 股交易约束引擎测试。"""
import pytest
from datetime import date

from trade_krono_cli.trading_constraints import (
    TradingConstraintResult,
    T1Tracker,
    compute_limit_prices,
    check_limit_status,
    enforce_t1,
    check_all_constraints,
    filter_by_constraints,
    compute_transaction_cost,
    detect_exchange,
)
from trade_krono_cli.constraints_config import ConstraintConfig


# ═══════════════════════════════════════════════════════
# detect_exchange
# ═══════════════════════════════════════════════════════

class TestDetectExchange:
    def test_sse(self):
        assert detect_exchange("sh.600519") == "sse"

    def test_szse_main(self):
        assert detect_exchange("sz.000001") == "szse"

    def test_szse_gem(self):
        assert detect_exchange("sz.300001") == "szse"

    def test_szse_star(self):
        assert detect_exchange("sh.688001") == "sse"

    def test_unknown(self):
        with pytest.raises(ValueError):
            detect_exchange("xx.123456")


# ═══════════════════════════════════════════════════════
# compute_limit_prices
# ═══════════════════════════════════════════════════════

class TestComputeLimitPrices:
    def test_sse_main_board(self):
        """主板 ±10%。"""
        cfg = ConstraintConfig(enable_limit_check=True)
        up, down = compute_limit_prices(100.0, "sh.600519", config=cfg)
        assert up == 110.0
        assert down == 90.0

    def test_szse_gem(self):
        """创业板/科创板 ±20%。"""
        cfg = ConstraintConfig(enable_limit_check=True)
        up, down = compute_limit_prices(100.0, "sz.300001", config=cfg)
        assert up == 120.0
        assert down == 80.0

    def test_szse_star(self):
        """科创板 ±20%。"""
        cfg = ConstraintConfig(enable_limit_check=True)
        up, down = compute_limit_prices(50.0, "sh.688001", config=cfg)
        assert up == 60.0
        assert down == 40.0

    def test_disabled(self):
        cfg = ConstraintConfig(enable_limit_check=False)
        up, down = compute_limit_prices(100.0, config=cfg)
        assert up is None
        assert down is None

    def test_zero_prev_close(self):
        cfg = ConstraintConfig(enable_limit_check=True)
        up, down = compute_limit_prices(0.0, config=cfg)
        assert up is None
        assert down is None


# ═══════════════════════════════════════════════════════
# check_limit_status
# ═══════════════════════════════════════════════════════

class TestCheckLimitStatus:
    def test_normal_price(self):
        """正常价格 → allowed=True。"""
        r = check_limit_status("sh.600519", current_price=105.0, prev_close=100.0)
        assert r.allowed is True
        assert r.reason is None

    def test_limit_up(self):
        """触及涨停 → allowed=False。"""
        r = check_limit_status("sh.600519", current_price=110.0, prev_close=100.0)
        assert r.allowed is False
        assert r.reason == "LIMIT_UP"
        assert r.limit_up_price == 110.0

    def test_limit_down(self):
        """触及跌停 → allowed=False。"""
        r = check_limit_status("sh.600519", current_price=90.0, prev_close=100.0)
        assert r.allowed is False
        assert r.reason == "LIMIT_DOWN"
        assert r.limit_down_price == 90.0

    def test_gem_limit_up(self):
        """创业板涨停价不同（±20%）。"""
        r = check_limit_status("sz.300001", current_price=120.0, prev_close=100.0)
        assert r.allowed is False
        assert r.reason == "LIMIT_UP"
        assert r.limit_up_price == 120.0

    def test_near_limit_up_tolerance(self):
        """允许 0.1% 浮点误差。"""
        r = check_limit_status("sh.600519", current_price=109.99, prev_close=100.0)
        assert r.allowed is False  # 109.99 >= 110 * 0.999 = 109.89


# ═══════════════════════════════════════════════════════
# T1Tracker / enforce_t1
# ═══════════════════════════════════════════════════════

class TestT1Tracker:
    def test_can_sell_no_record(self):
        tracker = T1Tracker()
        assert tracker.can_sell("sh.600519", "2026-08-12") is True

    def test_can_sell_next_day(self):
        tracker = T1Tracker()
        tracker.record_buy("sh.600519", "2026-08-11")
        assert tracker.can_sell("sh.600519", "2026-08-12") is True

    def test_cannot_sell_same_day(self):
        tracker = T1Tracker()
        tracker.record_buy("sh.600519", "2026-08-11")
        assert tracker.can_sell("sh.600519", "2026-08-11") is False

    def test_locked_until(self):
        tracker = T1Tracker()
        tracker.record_buy("sh.600519", "2026-08-11")
        assert tracker.locked_until("sh.600519") == date(2026, 8, 12)

    def test_locked_until_no_record(self):
        tracker = T1Tracker()
        assert tracker.locked_until("sh.600519") is None

    def test_clear(self):
        tracker = T1Tracker()
        tracker.record_buy("sh.600519", "2026-08-11")
        tracker.clear()
        assert tracker.can_sell("sh.600519", "2026-08-11") is True


class TestEnforceT1:
    def test_no_buy_record(self):
        tracker = T1Tracker()
        r = enforce_t1("sh.600519", "2026-08-12", tracker)
        assert r.allowed is True

    def test_t1_locked(self):
        tracker = T1Tracker()
        tracker.record_buy("sh.600519", "2026-08-11")
        r = enforce_t1("sh.600519", "2026-08-11", tracker)
        assert r.allowed is False
        assert "T1_LOCKED" in r.reason

    def test_t1_unlocked_next_day(self):
        tracker = T1Tracker()
        tracker.record_buy("sh.600519", "2026-08-11")
        r = enforce_t1("sh.600519", "2026-08-12", tracker)
        assert r.allowed is True

    def test_disabled(self):
        tracker = T1Tracker()
        tracker.record_buy("sh.600519", "2026-08-11")
        cfg = ConstraintConfig(enable_t1=False)
        r = enforce_t1("sh.600519", "2026-08-11", tracker, config=cfg)
        assert r.allowed is True


# ═══════════════════════════════════════════════════════
# compute_transaction_cost
# ═══════════════════════════════════════════════════════

class TestComputeTransactionCost:
    def test_buy_side(self):
        """买入扣 8bps。"""
        cfg = ConstraintConfig(commission_bps=3.0, slippage_bps=5.0, stamp_duty_bps=1.0)
        result = compute_transaction_cost(5.0, side="buy", config=cfg)
        assert result == pytest.approx(4.92, abs=0.01)  # 5 - 0.08

    def test_sell_side(self):
        """卖出扣 9bps。"""
        cfg = ConstraintConfig(commission_bps=3.0, slippage_bps=5.0, stamp_duty_bps=1.0)
        result = compute_transaction_cost(5.0, side="sell", config=cfg)
        assert result == pytest.approx(4.91, abs=0.01)  # 5 - 0.09

    def test_roundtrip(self):
        """双边共扣 17bps。"""
        cfg = ConstraintConfig(commission_bps=3.0, slippage_bps=5.0, stamp_duty_bps=1.0)
        result = compute_transaction_cost(5.0, side="roundtrip", config=cfg)
        assert result == pytest.approx(4.83, abs=0.01)  # 5 - 0.17

    def test_disabled(self):
        cfg = ConstraintConfig(enable_cost_model=False)
        result = compute_transaction_cost(5.0, side="roundtrip", config=cfg)
        assert result == 5.0


# ═══════════════════════════════════════════════════════
# check_all_constraints
# ═══════════════════════════════════════════════════════

class TestCheckAllConstraints:
    def test_all_pass(self):
        """无约束问题时通过。"""
        r = check_all_constraints(
            "sh.600519", "2026-08-12",
            current_price=105.0, prev_close=100.0,
        )
        assert r.allowed is True
        assert r.reason is None

    def test_limit_up_blocks(self):
        r = check_all_constraints(
            "sh.600519", "2026-08-12",
            current_price=110.0, prev_close=100.0,
        )
        assert r.allowed is False
        assert r.reason == "LIMIT_UP"

    def test_t1_blocks(self):
        tracker = T1Tracker()
        tracker.record_buy("sh.600519", "2026-08-11")
        r = check_all_constraints(
            "sh.600519", "2026-08-11",
            current_price=105.0, prev_close=100.0,
            t1_tracker=tracker,
        )
        assert r.allowed is False
        assert "T1_LOCKED" in r.reason

    def test_st_filter_disabled(self):
        """ST 过滤未启用时不过滤。"""
        cfg = ConstraintConfig(enable_st_filter=False)
        r = check_all_constraints(
            "sh.600519", "2026-08-12",
            current_price=105.0, prev_close=100.0,
            config=cfg,
        )
        assert r.allowed is True

    def test_check_st_status_no_baostock(self):
        """baostock 未安装时不应崩溃，返回 False。"""
        from unittest.mock import patch
        from trade_krono_cli.trading_constraints import check_st_status
        with patch.dict("sys.modules", {"baostock": None}):
            result = check_st_status("sh.600519")
        assert result is False


# ═══════════════════════════════════════════════════════
# filter_by_constraints
# ═══════════════════════════════════════════════════════

class TestFilterByConstraints:
    def test_all_pass(self):
        items = [
            {"ticker": "sh.600519", "date": "2026-08-12",
             "kronos_last_close": 100.0, "kronos_pred_close": 105.0},
            {"ticker": "sz.000858", "date": "2026-08-12",
             "kronos_last_close": 25.0, "kronos_pred_close": 26.0},
        ]
        allowed, rejected = filter_by_constraints(items)
        assert len(allowed) == 2
        assert len(rejected) == 0

    def test_limit_up_filtered(self):
        items = [
            {"ticker": "sh.600519", "date": "2026-08-12",
             "kronos_last_close": 100.0, "kronos_pred_close": 110.0},
        ]
        allowed, rejected = filter_by_constraints(items)
        assert len(allowed) == 0
        assert len(rejected) == 1
        assert rejected[0]["constraint_reason"] == "LIMIT_UP"

    def test_mixed(self):
        items = [
            {"ticker": "sh.600519", "date": "2026-08-12",
             "kronos_last_close": 100.0, "kronos_pred_close": 105.0},
            {"ticker": "sz.000858", "date": "2026-08-12",
             "kronos_last_close": 25.0, "kronos_pred_close": 30.0},  # 20%涨停
        ]
        allowed, rejected = filter_by_constraints(items)
        assert len(allowed) == 1
        assert len(rejected) == 1
        assert allowed[0]["ticker"] == "sh.600519"
