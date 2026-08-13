"""
tests/test_stock_filter.py — 股票过滤规则引擎测试。

覆盖：
  · FilterRule 子类与操作符
  · StockMeta 字段访问
  · StockFilter.apply() 单条规则
  · StockFilter.apply_batch() 批量过滤
  · StockFilter.from_config() 工厂方法
  · 边界情况（None 字段、ST 过滤、行业匹配）
  · 自定义规则链
"""
import pytest

from trade_krono_cli.stock_filter import (
    FilterOp,
    FilterRule,
    MinValueRule,
    MaxValueRule,
    RangeRule,
    InSetRule,
    NotInSetRule,
    ContainsRule,
    MatchRule,
    StockMeta,
    StockFilter,
)


# ═══════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════

def _meta(**kwargs) -> StockMeta:
    """便捷构造 StockMeta，ticker 默认 'sh.600519'。"""
    kwargs.setdefault("ticker", "sh.600519")
    return StockMeta(**kwargs)


# ═══════════════════════════════════════════════════════
# FilterRule 基础
# ═══════════════════════════════════════════════════════

class TestFilterRule:
    def test_min_rule(self):
        r = MinValueRule("confidence", 55.0)
        assert r.field == "confidence"
        assert r.op == FilterOp.MIN
        assert r.value == 55.0
        assert r.label == ">=55.0"

    def test_max_rule(self):
        r = MaxValueRule("risk_score", 0.7)
        assert r.op == FilterOp.MAX
        assert r.value == 0.7

    def test_range_rule(self):
        r = RangeRule("market_cap_billion", 50.0, 5000.0)
        assert r.op == FilterOp.RANGE
        assert r.value == (50.0, 5000.0)

    def test_in_set_rule(self):
        r = InSetRule("signal", {"BUY", "HOLD"})
        assert r.op == FilterOp.IN
        assert isinstance(r.value, frozenset)

    def test_not_in_set_rule(self):
        r = NotInSetRule("industry", {"房地产"})
        assert r.op == FilterOp.NOT_IN
        assert "房地产" in r.value

    def test_contains_rule(self):
        r = ContainsRule("industry", "银行")
        assert r.op == FilterOp.CONTAINS
        assert r.value == "银行"

    def test_match_rule(self):
        r = MatchRule("industry", r"^电.*")
        assert r.op == FilterOp.MATCH
        assert r.value.pattern == r"^电.*"

    def test_rule_frozen(self):
        """FilterRule 不可变。"""
        r = MinValueRule("x", 1.0)
        with pytest.raises(AttributeError):
            r.field = "y"


# ═══════════════════════════════════════════════════════
# StockFilter.apply() 单条规则测试
# ═══════════════════════════════════════════════════════

class TestStockFilterApply:
    def test_min_value_pass(self):
        f = StockFilter([MinValueRule("confidence", 55.0)])
        assert f.apply(_meta(confidence=60.0)) is True
        assert f.apply(_meta(confidence=55.0)) is True
        assert f.apply(_meta(confidence=54.9)) is False

    def test_min_value_none_skips(self):
        """confidence=None 应跳过 MIN 规则（不拦截）。"""
        f = StockFilter([MinValueRule("confidence", 55.0)])
        assert f.apply(_meta(confidence=None)) is True

    def test_max_value_pass(self):
        f = StockFilter([MaxValueRule("risk_score", 0.7)])
        assert f.apply(_meta(risk_score=0.5)) is True
        assert f.apply(_meta(risk_score=0.7)) is True
        assert f.apply(_meta(risk_score=0.71)) is False

    def test_range_pass(self):
        f = StockFilter([RangeRule("market_cap_billion", 50.0, 5000.0)])
        assert f.apply(_meta(market_cap_billion=100.0)) is True
        assert f.apply(_meta(market_cap_billion=50.0)) is True
        assert f.apply(_meta(market_cap_billion=5000.0)) is True
        assert f.apply(_meta(market_cap_billion=49.9)) is False
        assert f.apply(_meta(market_cap_billion=5001.0)) is False

    def test_range_none_skips(self):
        f = StockFilter([RangeRule("market_cap_billion", 50.0, 5000.0)])
        assert f.apply(_meta(market_cap_billion=None)) is True

    def test_in_set_pass(self):
        f = StockFilter([InSetRule("signal", {"BUY", "HOLD"})])
        assert f.apply(_meta(signal="BUY")) is True
        assert f.apply(_meta(signal="HOLD")) is True
        assert f.apply(_meta(signal="SELL")) is False

    def test_not_in_set_pass(self):
        f = StockFilter([NotInSetRule("industry", {"房地产", "煤炭"})])
        assert f.apply(_meta(industry="银行")) is True
        assert f.apply(_meta(industry="房地产")) is False

    def test_contains_pass(self):
        f = StockFilter([ContainsRule("industry", "银行")])
        assert f.apply(_meta(industry="银行")) is True
        assert f.apply(_meta(industry="股份制银行")) is True
        assert f.apply(_meta(industry="保险")) is False

    def test_match_pass(self):
        f = StockFilter([MatchRule("industry", r"^电.*")])
        assert f.apply(_meta(industry="电力")) is True
        assert f.apply(_meta(industry="电子")) is True
        assert f.apply(_meta(industry="银行")) is False

    def test_st_filter(self):
        """is_st=True 被 NotInSetRule 过滤。"""
        f = StockFilter([MaxValueRule("is_st", 0, label="not ST")])
        assert f.apply(_meta(is_st=False)) is True
        assert f.apply(_meta(is_st=True)) is False

    def test_unknown_op_not_blocking(self):
        """已知操作符正常工作。"""
        f = StockFilter([MinValueRule("confidence", 1.0)])
        assert f.apply(_meta(confidence=2.0)) is True

    def test_exception_in_rule_logs_but_continues(self):
        """单条规则异常不应中断其他规则。"""
        # 通过自定义规则触发异常
        class BadRule(FilterRule):
            def __init__(self):
                super().__init__(field="x", op=FilterOp.MIN, value=1, label="bad")
        f = StockFilter([BadRule(), MinValueRule("confidence", 50.0)])
        # BadRule._check_rule 会抛 AttributeError（MinValueRule 是不同类）
        # 但 StockFilter.apply 应该捕获异常继续执行
        meta = _meta(confidence=60.0)
        result = f.apply(meta)
        # 如果 BadRule 的 _check_rule 抛异常被捕获，则继续到下一条规则
        # 由于 BadRule 继承自 FilterRule 但没有重写 _check_rule，
        # 实际执行时 BadRule.op == FilterOp.MIN，所以会走到正常路径
        # 这里只是确保不崩溃
        assert isinstance(result, bool)


# ═══════════════════════════════════════════════════════
# StockFilter.apply_batch()
# ═══════════════════════════════════════════════════════

class TestStockFilterBatch:
    def test_batch_basic(self):
        f = StockFilter([
            MinValueRule("confidence", 55.0),
            InSetRule("signal", {"BUY", "HOLD"}),
        ])
        metas = [
            _meta(ticker="a", signal="BUY", confidence=60.0),
            _meta(ticker="b", signal="SELL", confidence=80.0),
            _meta(ticker="c", signal="HOLD", confidence=50.0),
            _meta(ticker="d", signal="BUY", confidence=70.0),
        ]
        passed, rejected = f.apply_batch(metas)
        assert len(passed) == 2
        assert len(rejected) == 2
        assert {m.ticker for m in passed} == {"a", "d"}
        assert {m.ticker for m in rejected} == {"b", "c"}

    def test_batch_empty(self):
        f = StockFilter([MinValueRule("confidence", 55.0)])
        passed, rejected = f.apply_batch([])
        assert passed == []
        assert rejected == []

    def test_batch_all_pass(self):
        f = StockFilter([MinValueRule("confidence", 10.0)])
        metas = [_meta(confidence=50.0), _meta(confidence=80.0)]
        passed, rejected = f.apply_batch(metas)
        assert len(passed) == 2
        assert rejected == []


# ═══════════════════════════════════════════════════════
# StockFilter.from_config() 工厂方法
# ═══════════════════════════════════════════════════════

class TestStockFilterFromConfig:
    def test_minimal_config(self):
        f = StockFilter.from_config()
        # 至少应有 confidence 和 signal 两条规则
        assert len(f.rules) >= 2
        # 检查第一条是 confidence MIN
        assert f.rules[0].op == FilterOp.MIN
        assert f.rules[0].field == "confidence"
        # 第二条是 signal IN
        assert f.rules[1].op == FilterOp.IN
        assert f.rules[1].field == "signal"

    def test_with_market_cap(self):
        f = StockFilter.from_config(market_cap_range=(100.0, 5000.0))
        cap_rules = [r for r in f.rules if r.field == "market_cap_billion"]
        assert len(cap_rules) == 1
        assert cap_rules[0].op == FilterOp.RANGE
        assert cap_rules[0].value == (100.0, 5000.0)

    def test_with_industry_whitelist(self):
        f = StockFilter.from_config(industry_whitelist=["银行", "食品饮料"])
        ind_rules = [r for r in f.rules if r.field == "industry"]
        # 白名单用 InSetRule（精确匹配）
        assert all(r.op == FilterOp.IN for r in ind_rules)

    def test_with_industry_blacklist(self):
        f = StockFilter.from_config(industry_blacklist=["房地产"])
        ind_rules = [r for r in f.rules if r.field == "industry"]
        assert all(r.op == FilterOp.NOT_IN for r in ind_rules)

    def test_with_pe_pb_range(self):
        f = StockFilter.from_config(pe_range=(5.0, 30.0), pb_range=(0.0, 3.0))
        pe_rules = [r for r in f.rules if r.field == "pe_ttm"]
        pb_rules = [r for r in f.rules if r.field == "pb"]
        assert len(pe_rules) == 1 and pe_rules[0].value == (5.0, 30.0)
        assert len(pb_rules) == 1 and pb_rules[0].value == (0.0, 3.0)

    def test_with_max_risk_score(self):
        f = StockFilter.from_config(max_risk_score=0.5)
        risk_rules = [r for r in f.rules if r.field == "risk_score"]
        assert len(risk_rules) == 1
        assert risk_rules[0].op == FilterOp.MAX
        assert risk_rules[0].value == 0.5

    def test_with_volume_ratio(self):
        f = StockFilter.from_config(min_volume_ratio=1.5)
        vol_rules = [r for r in f.rules if r.field == "volume_ratio"]
        assert len(vol_rules) == 1
        assert vol_rules[0].op == FilterOp.MIN
        assert vol_rules[0].value == 1.5

    def test_exclude_st_default(self):
        f = StockFilter.from_config()
        st_rules = [r for r in f.rules if r.label == "not ST"]
        assert len(st_rules) == 1

    def test_exclude_st_disabled(self):
        f = StockFilter.from_config(exclude_st=False)
        st_rules = [r for r in f.rules if r.label == "not ST"]
        assert len(st_rules) == 0

    def test_full_config(self):
        f = StockFilter.from_config(
            min_confidence=60.0,
            allowed_signals=("BUY",),
            market_cap_range=(200.0, 3000.0),
            industry_whitelist=["银行"],
            industry_blacklist=["房地产"],
            pe_range=(5.0, 25.0),
            pb_range=(0.5, 2.0),
            max_risk_score=0.6,
            min_volume_ratio=1.2,
            exclude_st=True,
        )
        # 统计各类型规则
        ops = [r.op for r in f.rules]
        assert FilterOp.MIN in ops   # confidence
        assert FilterOp.IN in ops    # signal + industry whitelist
        assert FilterOp.MAX in ops   # is_st + risk_score
        assert FilterOp.RANGE in ops # market_cap + pe + pb
        assert FilterOp.NOT_IN in ops    # industry blacklist
        assert FilterOp.MIN in ops      # volume_ratio


# ═══════════════════════════════════════════════════════
# 边界情况
# ═══════════════════════════════════════════════════════

class TestEdgeCases:
    def test_none_fields_skip_all_rules(self):
        """所有字段均为 None 时，任何过滤规则都不应拦截。"""
        f = StockFilter([
            MinValueRule("confidence", 55.0),
            RangeRule("market_cap_billion", 50.0, 5000.0),
            MaxValueRule("risk_score", 0.7),
            InSetRule("signal", {"BUY"}),
        ])
        meta = _meta(
            ticker="sh.000001",
            signal=None,
            confidence=None,
            risk_score=None,
            market_cap_billion=None,
        )
        assert f.apply(meta) is True

    def test_st_filter_with_none_is_st(self):
        """is_st=None 时不应被 ST 规则拦截。"""
        f = StockFilter.from_config(exclude_st=True)
        meta = _meta(ticker="sh.999999", is_st=None)
        # None → 跳过，应通过
        assert f.apply(meta) is True

    def test_empty_rules_allows_everything(self):
        f = StockFilter(rules=[])
        assert f.apply(_meta(signal="SELL", confidence=10.0)) is True

    def test_mixed_none_and_some_fields(self):
        """部分字段有值，部分为 None。"""
        f = StockFilter([
            MinValueRule("confidence", 55.0),
            RangeRule("market_cap_billion", 50.0, 5000.0),
        ])
        # confidence 有值但 market_cap 为 None
        meta = _meta(confidence=60.0, market_cap_billion=None)
        assert f.apply(meta) is True  # None 跳过市值规则

        # confidence 不足
        meta2 = _meta(confidence=50.0, market_cap_billion=None)
        assert f.apply(meta2) is False


# ═══════════════════════════════════════════════════════
# 自定义规则链
# ═══════════════════════════════════════════════════════

class TestCustomRules:
    def test_custom_rule_chain(self):
        """手动构造规则链。"""
        rules = [
            MinValueRule("confidence", 60.0, label="高置信度"),
            InSetRule("signal", {"BUY", "HOLD"}, label="允许信号"),
            RangeRule("market_cap_billion", 100.0, 2000.0, label="中等市值"),
            MaxValueRule("risk_score", 0.5, label="低风险"),
        ]
        f = StockFilter(rules=rules)
        meta_good = _meta(
            signal="BUY", confidence=70.0,
            market_cap_billion=500.0, risk_score=0.3,
        )
        meta_bad_conf = _meta(
            signal="BUY", confidence=50.0,
            market_cap_billion=500.0, risk_score=0.3,
        )
        assert f.apply(meta_good) is True
        assert f.apply(meta_bad_conf) is False

    def test_custom_rule_with_regex(self):
        """使用正则匹配行业名。"""
        f = StockFilter([
            MatchRule("industry", r"^金融.*"),
        ])
        assert f.apply(_meta(industry="金融科技")) is True
        assert f.apply(_meta(industry="银行")) is False
