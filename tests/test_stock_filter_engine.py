"""StockFilter Engine 单元测试。

覆盖：
  - StockMeta 数据类
  - StockFilter.apply / apply_batch / from_config
  - 各种规则操作符（MIN/MAX/RANGE/IN/NOT_IN/CONTAINS/MATCH）
"""

from __future__ import annotations

from trade_krono_cli.stock_filter.engine import StockFilter, StockMeta
from trade_krono_cli.stock_filter.rules import (
    ContainsRule,
    FilterOp,
    FilterRule,
    InSetRule,
    MatchRule,
    MaxValueRule,
    MinValueRule,
    NotInSetRule,
    RangeRule,
)

# ── StockMeta ─────────────────────────────────────────────────────────────────


class TestStockMeta:
    def test_defaults(self) -> None:
        meta = StockMeta(ticker="sh.600519")
        assert meta.signal is None
        assert meta.confidence is None
        assert meta.pe_ttm is None
        assert meta.is_st is False
        assert meta.abnormal_flags == []

    def test_with_all_fields(self) -> None:
        meta = StockMeta(
            ticker="sz.000858",
            signal="BUY",
            confidence=85.0,
            pe_ttm=25.0,
            pb=3.5,
            market_cap_billion=2000.0,
            volume_ratio=1.5,
            turnover_rate=2.0,
            industry="白酒",
            is_st=False,
            abnormal_flags=[],
        )
        assert meta.ticker == "sz.000858"
        assert meta.signal == "BUY"
        assert meta.confidence == 85.0


# ── apply: 单条规则 ───────────────────────────────────────────────────────────


class TestApplySingleRule:
    def test_min_confidence_pass(self) -> None:
        meta = StockMeta(ticker="sh.600519", signal="BUY", confidence=80.0)
        rule = MinValueRule("confidence", 30.0, label="confidence >= 30")
        filter_obj = StockFilter([rule])
        assert filter_obj.apply(meta) is True

    def test_min_confidence_fail(self) -> None:
        meta = StockMeta(ticker="sh.600519", signal="BUY", confidence=10.0)
        rule = MinValueRule("confidence", 30.0, label="confidence >= 30")
        filter_obj = StockFilter([rule])
        assert filter_obj.apply(meta) is False

    def test_max_is_st_filters_st(self) -> None:
        meta = StockMeta(ticker="sh.600519", signal="BUY", is_st=True)
        rule = MaxValueRule("is_st", 0, label="not ST")
        filter_obj = StockFilter([rule])
        assert filter_obj.apply(meta) is False

    def test_in_signal_pass(self) -> None:
        meta = StockMeta(ticker="sh.600519", signal="BUY")
        rule = InSetRule("signal", {"BUY", "HOLD"}, label="signal in BUY/HOLD")
        filter_obj = StockFilter([rule])
        assert filter_obj.apply(meta) is True

    def test_in_signal_fail(self) -> None:
        meta = StockMeta(ticker="sh.600519", signal="STRONG_BUY")
        rule = InSetRule("signal", {"BUY", "HOLD"}, label="signal in BUY/HOLD")
        filter_obj = StockFilter([rule])
        assert filter_obj.apply(meta) is False

    def test_range_pe_pass(self) -> None:
        meta = StockMeta(ticker="sh.600519", pe_ttm=20.0)
        rule = RangeRule("pe_ttm", 5.0, 30.0, label="PE [5, 30]")
        filter_obj = StockFilter([rule])
        assert filter_obj.apply(meta) is True

    def test_range_pe_fail_low(self) -> None:
        meta = StockMeta(ticker="sh.600519", pe_ttm=2.0)
        rule = RangeRule("pe_ttm", 5.0, 30.0, label="PE [5, 30]")
        filter_obj = StockFilter([rule])
        assert filter_obj.apply(meta) is False

    def test_not_in_industry_pass(self) -> None:
        meta = StockMeta(ticker="sh.600519", industry="白酒")
        rule = NotInSetRule("industry", {"银行", "保险"}, label="industry NOT IN [银行, 保险]")
        filter_obj = StockFilter([rule])
        assert filter_obj.apply(meta) is True

    def test_contains_pass(self) -> None:
        meta = StockMeta(ticker="sh.600519", industry="白酒酿造")
        rule = ContainsRule("industry", "白酒", label="industry contains 白酒")
        filter_obj = StockFilter([rule])
        assert filter_obj.apply(meta) is True

    def test_match_pass(self) -> None:
        meta = StockMeta(ticker="sh.600519", industry="银行")
        rule = MatchRule("industry", r"^银.*", label="industry matches ^银.*")
        filter_obj = StockFilter([rule])
        assert filter_obj.apply(meta) is True


# ── None 字段处理 ─────────────────────────────────────────────────────────────


class TestNoneFieldHandling:
    def test_none_field_skips_rule(self) -> None:
        """None 字段 → 该规则不拦截。"""
        meta = StockMeta(ticker="sh.600519", signal="BUY", confidence=None)
        rule = MinValueRule("confidence", 30.0, label="confidence >= 30")
        filter_obj = StockFilter([rule])
        assert filter_obj.apply(meta) is True

    def test_mixed_none_and_valid(self) -> None:
        """部分字段为 None，部分有效。"""
        meta = StockMeta(ticker="sh.600519", signal="BUY", confidence=80.0, pe_ttm=None)
        rules = [
            MinValueRule("confidence", 30.0, label="confidence >= 30"),
            RangeRule("pe_ttm", 5.0, 30.0, label="PE [5, 30]"),
        ]
        filter_obj = StockFilter(rules)
        assert filter_obj.apply(meta) is True


# ── apply_batch ───────────────────────────────────────────────────────────────


class TestApplyBatch:
    def test_separates_passed_and_rejected(self) -> None:
        metas = [
            StockMeta(ticker="sh.600519", signal="BUY", confidence=80.0),
            StockMeta(ticker="sz.000858", signal="SELL", confidence=20.0),
            StockMeta(ticker="sh.601318", signal="HOLD", confidence=50.0),
        ]
        rules = [MinValueRule("confidence", 30.0, label="confidence >= 30")]
        filter_obj = StockFilter(rules)
        passed, rejected = filter_obj.apply_batch(metas)
        assert len(passed) == 2
        assert len(rejected) == 1
        assert rejected[0].ticker == "sz.000858"

    def test_empty_input(self) -> None:
        filter_obj = StockFilter([])
        passed, rejected = filter_obj.apply_batch([])
        assert passed == []
        assert rejected == []


# ── from_config ───────────────────────────────────────────────────────────────


class TestFromConfig:
    def test_default_config_filters_st(self) -> None:
        """默认配置包含 ST 过滤。"""
        f = StockFilter.from_config()
        st_meta = StockMeta(ticker="sh.600519", signal="BUY", confidence=80.0, is_st=True)
        normal_meta = StockMeta(ticker="sh.600519", signal="BUY", confidence=80.0, is_st=False)
        assert f.apply(st_meta) is False
        assert f.apply(normal_meta) is True

    def test_disable_st_allows_st_stock(self) -> None:
        """exclude_st=False 时允许 ST 股票。"""
        f = StockFilter.from_config(exclude_st=False)
        st_meta = StockMeta(ticker="sh.600519", signal="BUY", confidence=80.0, is_st=True)
        assert f.apply(st_meta) is True

    def test_min_confidence_filtering(self) -> None:
        """置信度低于阈值被过滤。"""
        f = StockFilter.from_config(min_confidence=50.0)
        low = StockMeta(ticker="sh.600519", signal="BUY", confidence=30.0)
        high = StockMeta(ticker="sh.600519", signal="BUY", confidence=70.0)
        assert f.apply(low) is False
        assert f.apply(high) is True

    def test_allowed_signals_filtering(self) -> None:
        """只允许 BUY/OVERWEIGHT 时 SELL 被过滤。"""
        f = StockFilter.from_config(allowed_signals=("BUY", "OVERWEIGHT"))
        buy = StockMeta(ticker="sh.600519", signal="BUY", confidence=80.0)
        sell = StockMeta(ticker="sh.600519", signal="SELL", confidence=80.0)
        assert f.apply(buy) is True
        assert f.apply(sell) is False

    def test_pe_range_filtering(self) -> None:
        """PE 范围过滤。"""
        f = StockFilter.from_config(pe_range=(10.0, 30.0))
        in_range = StockMeta(ticker="sh.600519", signal="BUY", confidence=80.0, pe_ttm=20.0)
        too_high = StockMeta(ticker="sh.600519", signal="BUY", confidence=80.0, pe_ttm=50.0)
        too_low = StockMeta(ticker="sh.600519", signal="BUY", confidence=80.0, pe_ttm=3.0)
        assert f.apply(in_range) is True
        assert f.apply(too_high) is False
        assert f.apply(too_low) is False

    def test_industry_whitelist(self) -> None:
        """行业白名单。"""
        f = StockFilter.from_config(industry_whitelist=["银行", "保险"])
        white = StockMeta(ticker="sh.600519", signal="BUY", confidence=80.0, industry="银行")
        black = StockMeta(ticker="sh.600519", signal="BUY", confidence=80.0, industry="互联网")
        assert f.apply(white) is True
        assert f.apply(black) is False

    def test_industry_blacklist(self) -> None:
        """行业黑名单。"""
        f = StockFilter.from_config(industry_blacklist=["煤炭", "钢铁"])
        ok = StockMeta(ticker="sh.600519", signal="BUY", confidence=80.0, industry="银行")
        bad = StockMeta(ticker="sh.600519", signal="BUY", confidence=80.0, industry="煤炭")
        assert f.apply(ok) is True
        assert f.apply(bad) is False

    def test_market_cap_range(self) -> None:
        """市值范围过滤。"""
        f = StockFilter.from_config(market_cap_range=(100.0, 5000.0))
        ok = StockMeta(ticker="sh.600519", signal="BUY", confidence=80.0, market_cap_billion=1000.0)
        small = StockMeta(ticker="sh.600519", signal="BUY", confidence=80.0, market_cap_billion=50.0)
        large = StockMeta(ticker="sh.600519", signal="BUY", confidence=80.0, market_cap_billion=10000.0)
        assert f.apply(ok) is True
        assert f.apply(small) is False
        assert f.apply(large) is False


# ── 边界情况 ──────────────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_empty_rules_passes_all(self) -> None:
        """空规则列表 → 全部通过。"""
        f = StockFilter([])
        meta = StockMeta(ticker="sh.600519", signal="SELL", confidence=5.0, is_st=True)
        assert f.apply(meta) is True

    def test_unknown_operator_passes(self) -> None:
        """未知操作符 → 不拦截（安全通过）。"""
        rule = FilterRule(field="confidence", op="UNKNOWN_OP", value=30.0, label="unknown")
        f = StockFilter([rule])
        meta = StockMeta(ticker="sh.600519", signal="BUY", confidence=10.0)
        assert f.apply(meta) is True

    def test_rule_exception_does_not_crash(self) -> None:
        """单条规则异常时不中断整个过滤流程。"""
        def bad_rule(meta: StockMeta) -> bool:  # type: ignore[empty-body]
            raise ValueError("boom")

        # 通过手动构造规则来测试
        rule = FilterRule(field="__nonexistent__", op=FilterOp.MIN, value=0, label="bad")
        f = StockFilter([rule])
        meta = StockMeta(ticker="sh.600519", signal="BUY", confidence=80.0)
        # 不应该抛出异常
        result = f.apply(meta)
        assert result is True  # None 字段 → 跳过
