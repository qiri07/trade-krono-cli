"""A 股交易约束引擎测试。"""

from datetime import date

import pytest

from trade_krono_cli.constraints_config import ConstraintConfig
from trade_krono_cli.trading_constraints import (
    T1Tracker,
    _is_st_by_name,
    check_all_constraints,
    check_limit_status,
    check_st_status,
    compute_limit_prices,
    compute_transaction_cost,
    detect_exchange,
    enforce_t1,
    filter_by_constraints,
)

# ═══════════════════════════════════════════════════════
# detect_exchange
# ═══════════════════════════════════════════════════════


class TestDetectExchange:
    def test_sse(self) -> None:
        assert detect_exchange("sh.600519") == "sse"

    def test_szse_main(self) -> None:
        assert detect_exchange("sz.000001") == "szse"

    def test_szse_gem(self) -> None:
        assert detect_exchange("sz.300001") == "szse"

    def test_szse_star(self) -> None:
        assert detect_exchange("sh.688001") == "sse"

    def test_unknown(self) -> None:
        with pytest.raises(ValueError):
            detect_exchange("xx.123456")


# ═══════════════════════════════════════════════════════
# compute_limit_prices
# ═══════════════════════════════════════════════════════


class TestComputeLimitPrices:
    def test_sse_main_board(self) -> None:
        """主板 ±10%。"""
        cfg = ConstraintConfig(enable_limit_check=True)
        up, down = compute_limit_prices(100.0, "sh.600519", config=cfg)
        assert up == 110.0
        assert down == 90.0

    def test_szse_gem(self) -> None:
        """创业板/科创板 ±20%。"""
        cfg = ConstraintConfig(enable_limit_check=True)
        up, down = compute_limit_prices(100.0, "sz.300001", config=cfg)
        assert up == 120.0
        assert down == 80.0

    def test_szse_star(self) -> None:
        """科创板 ±20%。"""
        cfg = ConstraintConfig(enable_limit_check=True)
        up, down = compute_limit_prices(50.0, "sh.688001", config=cfg)
        assert up == 60.0
        assert down == 40.0

    def test_disabled(self) -> None:
        cfg = ConstraintConfig(enable_limit_check=False)
        up, down = compute_limit_prices(100.0, config=cfg)
        assert up is None
        assert down is None

    def test_zero_prev_close(self) -> None:
        cfg = ConstraintConfig(enable_limit_check=True)
        up, down = compute_limit_prices(0.0, config=cfg)
        assert up is None
        assert down is None


# ═══════════════════════════════════════════════════════
# check_limit_status
# ═══════════════════════════════════════════════════════


class TestCheckLimitStatus:
    def test_normal_price(self) -> None:
        """正常价格 → allowed=True。"""
        r = check_limit_status("sh.600519", current_price=105.0, prev_close=100.0)
        assert r.allowed is True
        assert r.reason is None

    def test_limit_up(self) -> None:
        """触及涨停 → allowed=False。"""
        r = check_limit_status("sh.600519", current_price=110.0, prev_close=100.0)
        assert r.allowed is False
        assert r.reason == "LIMIT_UP"
        assert r.limit_up_price == 110.0

    def test_limit_down(self) -> None:
        """触及跌停 → allowed=False。"""
        r = check_limit_status("sh.600519", current_price=90.0, prev_close=100.0)
        assert r.allowed is False
        assert r.reason == "LIMIT_DOWN"
        assert r.limit_down_price == 90.0

    def test_gem_limit_up(self) -> None:
        """创业板涨停价不同（±20%）。"""
        r = check_limit_status("sz.300001", current_price=120.0, prev_close=100.0)
        assert r.allowed is False
        assert r.reason == "LIMIT_UP"
        assert r.limit_up_price == 120.0

    def test_near_limit_up_tolerance(self) -> None:
        """允许 0.1% 浮点误差。"""
        r = check_limit_status("sh.600519", current_price=109.99, prev_close=100.0)
        assert r.allowed is False  # 109.99 >= 110 * 0.999 = 109.89


# ═══════════════════════════════════════════════════════
# T1Tracker / enforce_t1
# ═══════════════════════════════════════════════════════


class TestT1Tracker:
    def test_can_sell_no_record(self) -> None:
        tracker = T1Tracker()
        assert tracker.can_sell("sh.600519", "2026-08-12") is True

    def test_can_sell_next_day(self) -> None:
        tracker = T1Tracker()
        tracker.record_buy("sh.600519", "2026-08-11")
        assert tracker.can_sell("sh.600519", "2026-08-12") is True

    def test_cannot_sell_same_day(self) -> None:
        tracker = T1Tracker()
        tracker.record_buy("sh.600519", "2026-08-11")
        assert tracker.can_sell("sh.600519", "2026-08-11") is False

    def test_locked_until(self) -> None:
        tracker = T1Tracker()
        tracker.record_buy("sh.600519", "2026-08-11")
        assert tracker.locked_until("sh.600519") == date(2026, 8, 12)

    def test_locked_until_no_record(self) -> None:
        tracker = T1Tracker()
        assert tracker.locked_until("sh.600519") is None

    def test_clear(self) -> None:
        tracker = T1Tracker()
        tracker.record_buy("sh.600519", "2026-08-11")
        tracker.clear()
        assert tracker.can_sell("sh.600519", "2026-08-11") is True


class TestEnforceT1:
    def test_no_buy_record(self) -> None:
        tracker = T1Tracker()
        r = enforce_t1("sh.600519", "2026-08-12", tracker)
        assert r.allowed is True

    def test_t1_locked(self) -> None:
        tracker = T1Tracker()
        tracker.record_buy("sh.600519", "2026-08-11")
        r = enforce_t1("sh.600519", "2026-08-11", tracker)
        assert r.allowed is False
        assert "T1_LOCKED" in r.reason

    def test_t1_unlocked_next_day(self) -> None:
        tracker = T1Tracker()
        tracker.record_buy("sh.600519", "2026-08-11")
        r = enforce_t1("sh.600519", "2026-08-12", tracker)
        assert r.allowed is True

    def test_disabled(self) -> None:
        tracker = T1Tracker()
        tracker.record_buy("sh.600519", "2026-08-11")
        cfg = ConstraintConfig(enable_t1=False)
        r = enforce_t1("sh.600519", "2026-08-11", tracker, config=cfg)
        assert r.allowed is True


# ═══════════════════════════════════════════════════════
# compute_transaction_cost
# ═══════════════════════════════════════════════════════


class TestComputeTransactionCost:
    def test_buy_side(self) -> None:
        """买入扣 8bps。"""
        cfg = ConstraintConfig(commission_bps=3.0, slippage_bps=5.0, stamp_duty_bps=1.0)
        result = compute_transaction_cost(5.0, side="buy", config=cfg)
        assert result == pytest.approx(4.92, abs=0.01)  # 5 - 0.08

    def test_sell_side(self) -> None:
        """卖出扣 9bps。"""
        cfg = ConstraintConfig(commission_bps=3.0, slippage_bps=5.0, stamp_duty_bps=1.0)
        result = compute_transaction_cost(5.0, side="sell", config=cfg)
        assert result == pytest.approx(4.91, abs=0.01)  # 5 - 0.09

    def test_roundtrip(self) -> None:
        """双边共扣 17bps。"""
        cfg = ConstraintConfig(commission_bps=3.0, slippage_bps=5.0, stamp_duty_bps=1.0)
        result = compute_transaction_cost(5.0, side="roundtrip", config=cfg)
        assert result == pytest.approx(4.83, abs=0.01)  # 5 - 0.17

    def test_disabled(self) -> None:
        cfg = ConstraintConfig(enable_cost_model=False)
        result = compute_transaction_cost(5.0, side="roundtrip", config=cfg)
        assert result == 5.0


# ═══════════════════════════════════════════════════════
# check_all_constraints
# ═══════════════════════════════════════════════════════


class TestCheckAllConstraints:
    def test_all_pass(self) -> None:
        """无约束问题时通过。"""
        r = check_all_constraints(
            "sh.600519",
            "2026-08-12",
            current_price=105.0,
            prev_close=100.0,
        )
        assert r.allowed is True
        assert r.reason is None

    def test_limit_up_blocks(self) -> None:
        r = check_all_constraints(
            "sh.600519",
            "2026-08-12",
            current_price=110.0,
            prev_close=100.0,
        )
        assert r.allowed is False
        assert r.reason == "LIMIT_UP"

    def test_t1_blocks(self) -> None:
        tracker = T1Tracker()
        tracker.record_buy("sh.600519", "2026-08-11")
        r = check_all_constraints(
            "sh.600519",
            "2026-08-11",
            current_price=105.0,
            prev_close=100.0,
            t1_tracker=tracker,
        )
        assert r.allowed is False
        assert "T1_LOCKED" in r.reason

    def test_st_filter_disabled(self) -> None:
        """ST 过滤未启用时不过滤。"""
        cfg = ConstraintConfig(enable_st_filter=False)
        r = check_all_constraints(
            "sh.600519",
            "2026-08-12",
            current_price=105.0,
            prev_close=100.0,
            config=cfg,
        )
        assert r.allowed is True

    def test_check_st_status_no_baostock(self) -> None:
        """Baostock 未安装时不应崩溃，返回 False。"""
        from unittest.mock import patch

        from trade_krono_cli.trading_constraints import check_st_status

        with patch.dict("sys.modules", {"baostock": None}):
            result = check_st_status("sh.600519")
        assert result is False


# ═══════════════════════════════════════════════════════
# filter_by_constraints
# ═══════════════════════════════════════════════════════


class TestFilterByConstraints:
    def test_all_pass(self) -> None:
        items = [
            {
                "ticker": "sh.600519",
                "date": "2026-08-12",
                "kronos_last_close": 100.0,
                "kronos_pred_close": 105.0,
            },
            {
                "ticker": "sz.000858",
                "date": "2026-08-12",
                "kronos_last_close": 25.0,
                "kronos_pred_close": 26.0,
            },
        ]
        allowed, rejected = filter_by_constraints(items)
        assert len(allowed) == 2
        assert len(rejected) == 0

    def test_limit_up_filtered(self) -> None:
        items = [
            {
                "ticker": "sh.600519",
                "date": "2026-08-12",
                "kronos_last_close": 100.0,
                "kronos_pred_close": 110.0,
            },
        ]
        allowed, rejected = filter_by_constraints(items)
        assert len(allowed) == 0
        assert len(rejected) == 1
        assert rejected[0]["constraint_reason"] == "LIMIT_UP"

    def test_mixed(self) -> None:
        items = [
            {
                "ticker": "sh.600519",
                "date": "2026-08-12",
                "kronos_last_close": 100.0,
                "kronos_pred_close": 105.0,
            },
            {
                "ticker": "sz.000858",
                "date": "2026-08-12",
                "kronos_last_close": 25.0,
                "kronos_pred_close": 30.0,
            },  # 20%涨停
        ]
        allowed, rejected = filter_by_constraints(items)
        assert len(allowed) == 1
        assert len(rejected) == 1
        assert allowed[0]["ticker"] == "sh.600519"


# ═══════════════════════════════════════════════════════
# _is_st_by_name
# ═══════════════════════════════════════════════════════


class TestIsStByName:
    def test_with_st_name_hint(self) -> None:
        assert _is_st_by_name("sh.600001", "ST某某") is True

    def test_with_star_st_name_hint(self) -> None:
        assert _is_st_by_name("sh.600002", "*ST某某") is True

    def test_with_sst_name_hint(self) -> None:
        assert _is_st_by_name("sh.600003", "SST某某") is True

    def test_with_n_st_name_hint(self) -> None:
        assert _is_st_by_name("sh.600004", "N ST某某") is True

    def test_without_name_hint_returns_false(self) -> None:
        """No name_hint → heuristic returns False (later confirmed by query)."""
        assert _is_st_by_name("sh.600519") is False

    def test_normal_name_returns_false(self) -> None:
        assert _is_st_by_name("sh.600519", "贵州茅台") is False


# ═══════════════════════════════════════════════════════
# check_st_status — error paths
# ═══════════════════════════════════════════════════════


class TestCheckStStatusErrorPaths:
    def test_runtime_error_from_provider(self) -> None:
        """BaostockProvider raises RuntimeError → returns False gracefully."""
        from unittest.mock import patch

        with patch(
            "trade_krono_cli.data_providers.baostock_provider.BaostockProvider",
            side_effect=RuntimeError("session invalid"),
        ):
            result = check_st_status("sh.600519")
        assert result is False

    def test_generic_exception_from_provider(self) -> None:
        """BaostockProvider raises generic Exception → returns False gracefully."""
        from unittest.mock import patch

        with patch(
            "trade_krono_cli.data_providers.baostock_provider.BaostockProvider",
            side_effect=Exception("network timeout"),
        ):
            result = check_st_status("sh.600519")
        assert result is False

    def test_st_detected_logs_and_returns_true(self) -> None:
        """ST stock detected → logs info and returns True."""
        from unittest.mock import MagicMock, patch

        mock_provider = MagicMock()
        mock_provider.check_st_status.return_value = True

        with patch(
            "trade_krono_cli.data_providers.baostock_provider.BaostockProvider",
            return_value=mock_provider,
        ):
            cfg = ConstraintConfig(enable_st_filter=True)
            result = check_st_status("sh.600001", config=cfg)
        assert result is True


# ═══════════════════════════════════════════════════════
# detect_exchange — bse prefix
# ═══════════════════════════════════════════════════════


class TestDetectExchangeBse:
    def test_bj_prefix(self) -> None:
        assert detect_exchange("bj.830001") == "bse"

    def test_bj_prefix_with_real_bj_stock(self) -> None:
        assert detect_exchange("bj.872925") == "bse"


# ═══════════════════════════════════════════════════════
# check_limit_status — near-limit tolerance
# ═══════════════════════════════════════════════════════


class TestCheckLimitStatusTolerance:
    def test_near_limit_up_within_tolerance(self) -> None:
        """Price at 99.9% of limit_up → still detected as LIMIT_UP."""
        r = check_limit_status("sh.600519", current_price=109.89, prev_close=100.0)
        assert r.allowed is False
        assert r.reason == "LIMIT_UP"

    def test_near_limit_down_within_tolerance(self) -> None:
        """Price slightly below limit_down → still detected as LIMIT_DOWN."""
        # 90.0 * 1.001 = 90.08999..., so 90.08 triggers the limit_down check
        r = check_limit_status("sh.600519", current_price=90.08, prev_close=100.0)
        assert r.allowed is False
        assert r.reason == "LIMIT_DOWN"


# ═══════════════════════════════════════════════════════
# compute_transaction_cost — disabled cost model
# ═══════════════════════════════════════════════════════


class TestComputeTransactionCostDisabled:
    def test_sell_no_cost_model(self) -> None:
        """enable_cost_model=False → sell returns gross return unchanged."""
        cfg = ConstraintConfig(enable_cost_model=False)
        result = compute_transaction_cost(5.0, side="sell", config=cfg)
        assert result == 5.0

    def test_roundtrip_no_cost_model(self) -> None:
        """enable_cost_model=False → roundtrip returns gross return unchanged."""
        cfg = ConstraintConfig(enable_cost_model=False)
        result = compute_transaction_cost(5.0, side="roundtrip", config=cfg)
        assert result == 5.0

    def test_unknown_side_returns_gross(self) -> None:
        """Unknown side string → returns gross_return_pct unchanged."""
        cfg = ConstraintConfig()
        result = compute_transaction_cost(5.0, side="hold", config=cfg)
        assert result == 5.0


# ═══════════════════════════════════════════════════════
# T1Tracker — invalid date format
# ═══════════════════════════════════════════════════════


class TestT1TrackerInvalidDate:
    def test_locked_until_bad_date_format(self) -> None:
        """Invalid date format in record_buy → locked_until returns None."""
        tracker = T1Tracker()
        tracker.record_buy("sh.600519", "not-a-date")
        assert tracker.locked_until("sh.600519") is None


# ═══════════════════════════════════════════════════════
# check_all_constraints — ST filter enabled path
# ═══════════════════════════════════════════════════════


class TestCheckAllConstraintsStFilter:
    def test_st_filter_enabled_st_detected(self) -> None:
        """ST filter enabled and stock is ST → returns ST_FILTER immediately."""
        from unittest.mock import MagicMock, patch

        mock_provider = MagicMock()
        mock_provider.check_st_status.return_value = True

        with patch(
            "trade_krono_cli.data_providers.baostock_provider.BaostockProvider",
            return_value=mock_provider,
        ):
            cfg = ConstraintConfig(enable_st_filter=True, enable_limit_check=False)
            r = check_all_constraints("sh.600001", "2026-08-12", config=cfg)
        assert r.allowed is False
        assert r.reason == "ST_FILTER"
        assert r.is_st is True

    def test_st_filter_disabled_st_status_ignored(self) -> None:
        """ST filter disabled → check_st_status not called, proceeds to other checks."""
        cfg = ConstraintConfig(enable_st_filter=False, enable_limit_check=False)
        r = check_all_constraints("sh.600001", "2026-08-12", config=cfg)
        assert r.allowed is True
