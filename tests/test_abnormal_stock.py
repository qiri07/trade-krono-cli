"""tests/test_abnormal_stock.py — 异常股票检测与标记测试。

覆盖：
  · StockAbnormality 枚举与 AbnormalityFlag
  · check_kline_completeness() K 线完整性校验
  · apply_abnormality_risk_boost() 风险分上调
  · precheck_stock_status() 批量预检（mock baostock）
  · _check_new_stock / _check_delisted 边界情况
  · StockMeta 异常字段
"""

from datetime import timedelta

import pandas as pd
import pytest

from trade_krono_cli.abnormal_stock import (
    AbnormalityFlag,
    StockAbnormality,
    _compute_severity,
    apply_abnormality_risk_boost,
    check_kline_completeness,
    precheck_stock_status,
)
from trade_krono_cli.stock_filter import StockMeta

# ═══════════════════════════════════════════════════════
# AbnormalityFlag 基础
# ═══════════════════════════════════════════════════════


class TestAbnormalityFlag:
    def test_normal_flag(self) -> None:
        f = AbnormalityFlag(ticker="sh.600519")
        assert f.is_normal is True
        assert f.flags == []
        assert f.severity == 0.0

    def test_st_flag(self) -> None:
        f = AbnormalityFlag(
            ticker="sh.600001",
            flags=[StockAbnormality.ST],
            severity=0.7,
            reason="ST 标的",
        )
        assert f.is_normal is False
        assert f.flag_names() == ["ST"]

    def test_multi_flags(self) -> None:
        f = AbnormalityFlag(
            ticker="sh.999999",
            flags=[StockAbnormality.ST, StockAbnormality.SUSPENDED],
            severity=0.9,
        )
        assert set(f.flag_names()) == {"ST", "SUSPENDED"}

    def test_frozen(self) -> None:
        f = AbnormalityFlag(ticker="sh.600519")
        with pytest.raises(AttributeError):
            f.ticker = "x"


# ═══════════════════════════════════════════════════════
# check_kline_completeness
# ═══════════════════════════════════════════════════════


class TestCheckKlineCompleteness:
    def _make_df(self, n_rows=400, gap_at=None):
        """构造连续交易日 DataFrame。"""
        dates = pd.bdate_range(end=pd.Timestamp("2026-08-13"), periods=n_rows)
        if gap_at is not None and gap_at < n_rows:
            # 在 gap_at 位置插入一个 5 天空白
            dates_list = list(dates)
            before = dates_list[:gap_at]
            after_start = dates_list[gap_at] + timedelta(days=5)
            after = pd.bdate_range(start=after_start, periods=n_rows - gap_at)
            dates = pd.DatetimeIndex(list(before) + list(after))
        return pd.DataFrame(
            {
                "timestamps": dates,
                "open": [100.0] * len(dates),
                "high": [101.0] * len(dates),
                "low": [99.0] * len(dates),
                "close": [100.5] * len(dates),
                "volume": [1000.0] * len(dates),
                "amount": [100000.0] * len(dates),
            },
        )

    def test_complete_data_passes(self) -> None:
        df = self._make_df(n_rows=400)
        passed, reason = check_kline_completeness(df, "sh.600519")
        assert passed is True
        assert "完整率" in reason

    def test_empty_df_fails(self) -> None:
        df = pd.DataFrame({"timestamps": [], "close": []})
        passed, reason = check_kline_completeness(df, "sh.600519")
        assert passed is False
        assert "空" in reason

    def test_none_df_fails(self) -> None:
        passed, _reason = check_kline_completeness(None, "sh.600519")
        assert passed is False

    def test_missing_timestamps_column(self) -> None:
        df = pd.DataFrame({"close": [1.0, 2.0]})
        passed, reason = check_kline_completeness(df, "sh.600519")
        assert passed is False
        assert "timestamps" in reason

    def test_with_gap_below_threshold(self) -> None:
        """5% 缺失率，低于 85% 阈值 → 不通过。"""
        df = self._make_df(n_rows=100, gap_at=50)
        passed, _reason = check_kline_completeness(df, "sh.600519", min_completeness=0.95)
        # 有较大缺口，完整率低于 95%
        assert passed is False

    def test_custom_threshold(self) -> None:
        df = self._make_df(n_rows=400)
        # 极低阈值，应通过
        passed, _ = check_kline_completeness(df, "sh.600519", min_completeness=0.01)
        assert passed is True

    def test_all_nan_in_column(self) -> None:
        """所有 close 为 NaN → 应被 dropna 后行数极少。"""
        df = pd.DataFrame(
            {
                "timestamps": pd.bdate_range(end=pd.Timestamp("2026-08-13"), periods=10),
                "close": [float("nan")] * 10,
            },
        )
        passed, _reason = check_kline_completeness(df, "sh.600519")
        # 有数据但 close 全 NaN，行数仍 10，完整率 100%，但无实际意义
        # 此测试仅确保不崩溃
        assert isinstance(passed, bool)


# ═══════════════════════════════════════════════════════
# apply_abnormality_risk_boost
# ═══════════════════════════════════════════════════════


class TestApplyRiskBoost:
    def test_no_flags(self) -> None:
        assert apply_abnormality_risk_boost(40.0, []) == 40.0

    def test_single_st(self) -> None:
        result = apply_abnormality_risk_boost(40.0, ["ST"])
        assert result == 60.0

    def test_single_suspended(self) -> None:
        result = apply_abnormality_risk_boost(40.0, ["SUSPENDED"])
        assert result == 70.0

    def test_multiple_flags(self) -> None:
        result = apply_abnormality_risk_boost(40.0, ["ST", "SUSPENDED"])
        assert result == 90.0

    def test_capped_at_100(self) -> None:
        result = apply_abnormality_risk_boost(80.0, ["ST", "SUSPENDED", "DELISTED"])
        assert result == 100.0

    def test_delisted_heavy(self) -> None:
        result = apply_abnormality_risk_boost(30.0, ["DELISTED"])
        assert result == 80.0

    def test_new_stock_light(self) -> None:
        result = apply_abnormality_risk_boost(50.0, ["NEW_STOCK"])
        assert result == 60.0

    def test_data_insufficient(self) -> None:
        result = apply_abnormality_risk_boost(50.0, ["DATA_INSUFFICIENT"])
        assert result == 65.0

    def test_disabled(self) -> None:
        result = apply_abnormality_risk_boost(40.0, ["ST"], enabled=False)
        assert result == 40.0

    def test_unknown_flag_safe(self) -> None:
        """未知标记不应崩溃。"""
        result = apply_abnormality_risk_boost(40.0, ["UNKNOWN_FLAG"])
        assert result == 40.0


# ═══════════════════════════════════════════════════════
# _compute_severity
# ═══════════════════════════════════════════════════════


class TestComputeSeverity:
    def test_empty(self) -> None:
        assert _compute_severity([]) == 0.0

    def test_st(self) -> None:
        assert _compute_severity([StockAbnormality.ST]) == 0.7

    def test_suspended(self) -> None:
        assert _compute_severity([StockAbnormality.SUSPENDED]) == 0.9

    def test_delisted(self) -> None:
        assert _compute_severity([StockAbnormality.DELISTED]) == 1.0

    def test_multiple_takes_max(self) -> None:
        assert (
            _compute_severity(
                [
                    StockAbnormality.NEW_STOCK,
                    StockAbnormality.ST,
                ],
            )
            == 0.7
        )  # max(0.3, 0.7)

    def test_new_stock(self) -> None:
        assert _compute_severity([StockAbnormality.NEW_STOCK]) == 0.3


# ═══════════════════════════════════════════════════════
# precheck_stock_status（mock BaostockProvider）
# ═══════════════════════════════════════════════════════


class TestPrecheckStockStatus:
    def test_all_normal(self, monkeypatch) -> None:
        """所有股票正常时，不产生任何异常标记。"""
        from trade_krono_cli.data_providers.baostock_provider import BaostockProvider

        monkeypatch.setattr(BaostockProvider, "check_st_status", lambda self, t: False)
        monkeypatch.setattr(BaostockProvider, "check_delisted", lambda self, t: False)
        monkeypatch.setattr(
            BaostockProvider, "check_new_stock", lambda self, t, d, n=60: (False, ""),
        )

        import trade_krono_cli.abnormal_stock as m

        m._check_st_status_cached.clear()

        results = precheck_stock_status(
            tickers=["sh.600519", "sz.000001"],
            eval_date="2026-08-13",
            skip_suspended=False,
            skip_new_stock=False,
        )
        for flag in results.values():
            assert flag.is_normal is True
            assert flag.flags == []

    def test_st_detection(self, monkeypatch) -> None:
        """模拟 ST 股票检测。"""
        from trade_krono_cli.data_providers.baostock_provider import BaostockProvider

        monkeypatch.setattr(BaostockProvider, "check_st_status", lambda self, t: True)
        monkeypatch.setattr(BaostockProvider, "check_delisted", lambda self, t: False)
        monkeypatch.setattr(
            BaostockProvider, "check_new_stock", lambda self, t, d, n=60: (False, ""),
        )

        import trade_krono_cli.abnormal_stock as m

        m._check_st_status_cached.clear()

        results = precheck_stock_status(
            tickers=["sh.600001"],
            eval_date="2026-08-13",
            skip_suspended=False,
            skip_new_stock=False,
        )
        flag = results["sh.600001"]
        assert StockAbnormality.ST in flag.flags
        assert "ST" in flag.reason

    def test_delisted_detection(self, monkeypatch) -> None:
        """模拟退市股票检测。"""
        from trade_krono_cli.data_providers.baostock_provider import BaostockProvider

        monkeypatch.setattr(BaostockProvider, "check_st_status", lambda self, t: False)
        monkeypatch.setattr(BaostockProvider, "check_delisted", lambda self, t: True)
        monkeypatch.setattr(
            BaostockProvider, "check_new_stock", lambda self, t, d, n=60: (False, ""),
        )

        import trade_krono_cli.abnormal_stock as m

        m._check_st_status_cached.clear()

        results = precheck_stock_status(
            tickers=["sh.600002"],
            eval_date="2026-08-13",
            skip_suspended=False,
            skip_new_stock=False,
        )
        flag = results["sh.600002"]
        assert StockAbnormality.DELISTED in flag.flags

    def test_new_stock_detection(self, monkeypatch) -> None:
        """模拟次新股检测（IPO 日期很近）。"""
        from trade_krono_cli.data_providers.baostock_provider import BaostockProvider

        monkeypatch.setattr(BaostockProvider, "check_st_status", lambda self, t: False)
        monkeypatch.setattr(BaostockProvider, "check_delisted", lambda self, t: False)
        monkeypatch.setattr(
            BaostockProvider,
            "check_new_stock",
            lambda self, t, d, n=60: (True, f"{t}: 次新股"),
        )

        import trade_krono_cli.abnormal_stock as m

        m._check_st_status_cached.clear()

        results = precheck_stock_status(
            tickers=["sh.600003"],
            eval_date="2026-08-13",
            min_listing_days=60,
            skip_suspended=False,
            skip_new_stock=True,
        )
        flag = results["sh.600003"]
        assert StockAbnormality.NEW_STOCK in flag.flags

    def test_skip_suspended_false(self, monkeypatch) -> None:
        """skip_suspended=False 时，停牌检测不执行。"""
        from trade_krono_cli.data_providers.baostock_provider import BaostockProvider

        monkeypatch.setattr(BaostockProvider, "check_st_status", lambda self, t: False)
        monkeypatch.setattr(BaostockProvider, "check_delisted", lambda self, t: False)
        monkeypatch.setattr(
            BaostockProvider, "check_new_stock", lambda self, t, d, n=60: (False, ""),
        )

        import trade_krono_cli.abnormal_stock as m

        m._check_st_status_cached.clear()

        results = precheck_stock_status(
            tickers=["sh.600519"],
            eval_date="2026-08-13",
            skip_suspended=False,
            skip_new_stock=False,
        )
        flag = results["sh.600519"]
        assert StockAbnormality.SUSPENDED not in flag.flags


# ═══════════════════════════════════════════════════════
# StockMeta 异常字段
# ═══════════════════════════════════════════════════════


class TestStockMetaAbnormal:
    def test_default_values(self) -> None:
        meta = StockMeta(ticker="sh.600519")
        assert meta.abnormal_flags == []
        assert meta.abnormality_score == 0.0

    def test_with_flags(self) -> None:
        meta = StockMeta(
            ticker="sh.600001",
            abnormal_flags=["ST", "SUSPENDED"],
            abnormality_score=0.9,
        )
        assert meta.abnormal_flags == ["ST", "SUSPENDED"]
        assert meta.abnormality_score == 0.9


# ═══════════════════════════════════════════════════════
# 工具函数导入
# ═══════════════════════════════════════════════════════
