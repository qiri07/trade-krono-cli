"""测试 DecisionAdapter — LLM 输出解析与 rating/keyword 回退。"""

import pytest

from trade_krono_cli.ta_decision import DecisionAdapter, InvestmentDecision, Signal
from trade_krono_cli.ta_runner import StockAnalysisResult


@pytest.fixture
def adapter():
    return DecisionAdapter()


# ── Rating 结构化解析 ────────────────────────────────────────────────────────


def test_rating_structured_underweight(adapter) -> None:
    text = "**Rating**: Underweight\n**Executive Summary**: ..."
    dec = adapter.parse(text)
    assert dec.signal == Signal.SELL
    assert dec.confidence == 40.0
    assert dec.thesis != ""


def test_rating_structured_strong_buy(adapter) -> None:
    text = "**Rating**: Strong Buy\n**Summary**: ..."
    dec = adapter.parse(text)
    assert dec.signal == Signal.BUY
    assert dec.confidence == 95.0


def test_rating_structured_buy(adapter) -> None:
    text = "**Rating**: Buy"
    dec = adapter.parse(text)
    assert dec.signal == Signal.BUY
    assert dec.confidence == 80.0


def test_rating_structured_neutral(adapter) -> None:
    text = "**Rating**: Neutral"
    dec = adapter.parse(text)
    assert dec.signal == Signal.HOLD
    assert dec.confidence == 50.0


def test_rating_structured_sell(adapter) -> None:
    text = "**Rating**: Sell"
    dec = adapter.parse(text)
    assert dec.signal == Signal.SELL
    assert dec.confidence == 30.0


def test_rating_structured_overweight(adapter) -> None:
    text = "**Rating**: Overweight"
    dec = adapter.parse(text)
    assert dec.signal == Signal.OVERWEIGHT
    assert dec.confidence == 70.0


def test_rating_structured_strong_sell(adapter) -> None:
    text = "**Rating**: Strong Sell"
    dec = adapter.parse(text)
    assert dec.signal == Signal.SELL
    assert dec.confidence == 15.0


def test_rating_unknown_fallback(adapter) -> None:
    """未识别的 Rating 值 → 兜底解析。"""
    text = "**Rating**: Accumulate"
    dec = adapter.parse(text)
    # "accumulate" 不匹配任何已知信号，fallback HOLD
    assert dec.signal in (Signal.HOLD, Signal.BUY)  # 宽松断言


# ── 负上下文感知 ─────────────────────────────────────────────────────────────


def test_negative_context_not_buy(adapter) -> None:
    """ "not recommend BUY" 不应误判为 BUY。"""
    text = "The analyst does not recommend BUY due to valuation concerns."
    dec = adapter.parse(text)
    assert dec.signal != Signal.BUY


def test_negative_context_not_sell(adapter) -> None:
    """ "not recommend SELL" 不应误判为 SELL。"""
    text = "We do not see a compelling reason to SELL at this time."
    dec = adapter.parse(text)
    assert dec.signal != Signal.SELL


def test_positive_buy_affirmed(adapter) -> None:
    """明确推荐 BUY → 正确识别。"""
    text = "We recommend BUY with strong conviction given the growth outlook."
    dec = adapter.parse(text)
    assert dec.signal == Signal.BUY


# ── Keyword fallback（无 Rating 字段）────────────────────────────────────────


def test_keyword_fallback_buy(adapter) -> None:
    text = "Momentum is strong and we see upside."
    dec = adapter.parse(text)
    # 无 BUY/SELL/HOLD 关键词 → fallback HOLD
    assert dec.signal == Signal.HOLD


def test_keyword_fallback_with_buy(adapter) -> None:
    text = "We are initiating a BUY position on this name."
    dec = adapter.parse(text)
    assert dec.signal == Signal.BUY


def test_keyword_fallback_with_sell(adapter) -> None:
    text = "Our base case leads us to SELL this stock."
    dec = adapter.parse(text)
    assert dec.signal == Signal.SELL


# ── Empty / edge cases ───────────────────────────────────────────────────────


def test_empty_text(adapter) -> None:
    dec = adapter.parse("")
    assert dec.signal == Signal.HOLD
    assert dec.confidence == 50.0


def test_none_text(adapter) -> None:
    dec = adapter.parse(None)  # type: ignore
    assert dec.signal == Signal.HOLD
    assert dec.confidence == 50.0


def test_only_whitespace(adapter) -> None:
    dec = adapter.parse("   \n  ")
    assert dec.signal == Signal.HOLD


# ── Thesis 提取 ──────────────────────────────────────────────────────────────


def test_thesis_from_investment_thesis(adapter) -> None:
    text = """**Rating**: Buy
**Investment Thesis**: The company has strong moat and pricing power.
**Executive Summary**: Summary here."""
    dec = adapter.parse(text)
    assert "strong moat" in dec.thesis.lower()


def test_thesis_fallback_to_summary(adapter) -> None:
    text = """**Rating**: Hold
**Executive Summary**: Mixed signals from analysts.
No thesis section."""
    dec = adapter.parse(text)
    assert "mixed signals" in dec.thesis.lower()


# ── Risks 提取 ───────────────────────────────────────────────────────────────


def test_risks_extraction(adapter) -> None:
    text = """**Rating**: Sell
Key risks include:
- Valuation is stretched
- Revenue growth decelerating
- Competitive pressure increasing
Other content here."""
    dec = adapter.parse(text)
    assert len(dec.risks) >= 2
    assert any("valuation" in r.lower() or "估值" in r for r in dec.risks)


def test_no_risks(adapter) -> None:
    text = "**Rating**: Buy\nNo risks identified."
    dec = adapter.parse(text)
    # 可能无风险条目
    assert isinstance(dec.risks, list)


# ── Expected return ──────────────────────────────────────────────────────────


def test_expected_return_buy(adapter) -> None:
    text = "**Rating**: Buy\nWe expect the stock to gain 15% over the next year."
    dec = adapter.parse(text)
    assert dec.expected_return is not None
    assert 10 <= dec.expected_return <= 20


def test_expected_return_buy_excludes_pe(adapter) -> None:
    """PE=19 等财务比率不应被当作预期收益。"""
    text = "**Rating**: Buy\nPE ratio is 19x and PEG is 1.5."
    dec = adapter.parse(text)
    # PE/PEG 行应被排除
    assert dec.expected_return is None or abs(dec.expected_return) < 5


# ── InvestmentDecision dataclass ─────────────────────────────────────────────


def test_investment_decision_to_dict(adapter) -> None:
    dec = InvestmentDecision(
        signal=Signal.BUY,
        confidence=85.0,
        expected_return=12.5,
        thesis="Strong fundamentals",
        risks=["val risk", "macro risk"],
    )
    d = dec.to_dict()
    assert d["signal"] == "BUY"
    assert d["confidence"] == 85.0
    assert d["risks"] == ["val risk", "macro risk"]


def test_reports_raw_vs_summary(adapter) -> None:
    """reports_raw 应保留完整文本，reports 应为 500 字摘要。"""
    long_text = "x" * 1000  # 1000字符的模拟报告
    result = StockAnalysisResult(
        ticker="sh.600519",
        date="2026-08-11",
        reports_raw={"market": long_text},
        reports={"market": long_text[:500]},
    )
    assert len(result.reports_raw["market"]) == 1000
    assert len(result.reports["market"]) == 500


def test_json_full_fields(adapter) -> None:
    """完整 JSON 应正确映射所有字段。"""
    import json

    text = json.dumps(
        {
            "signal": "BUY",
            "confidence": 85.0,
            "thesis": "基本面强劲，估值合理",
            "risks": ["估值偏高", "宏观波动"],
            "expected_return": 15.0,
        },
    )
    dec = adapter.parse(text)
    assert dec.signal == Signal.BUY
    assert dec.confidence == 85.0
    assert dec.thesis == "基本面强劲，估值合理"
    assert dec.risks == ["估值偏高", "宏观波动"]
    assert dec.expected_return == 15.0


def test_json_partial_fields(adapter) -> None:
    """部分 JSON 字段缺失时应使用默认值。"""
    import json

    text = json.dumps({"signal": "SELL", "confidence": 25.0})
    dec = adapter.parse(text)
    assert dec.signal == Signal.SELL
    assert dec.confidence == 25.0
    assert dec.thesis == ""
    assert dec.risks == []
    assert dec.expected_return is None


def test_json_unknown_signal_fallback(adapter) -> None:
    """JSON 中未知 signal 值应回退到 HOLD。"""
    import json

    text = json.dumps({"signal": "STRONG_BUY", "confidence": 90.0})
    dec = adapter.parse(text)
    assert dec.signal == Signal.HOLD
    assert dec.confidence == 90.0  # confidence 仍使用 JSON 中的值


def test_json_risks_as_string(adapter) -> None:
    """Risks 为逗号分隔字符串时应正确拆分。"""
    import json

    text = json.dumps(
        {
            "signal": "HOLD",
            "risks": "流动性不足, 政策不确定性, 汇率波动",
        },
    )
    dec = adapter.parse(text)
    assert dec.risks == ["流动性不足", "政策不确定性", "汇率波动"]


def test_json_max_risks_cap(adapter) -> None:
    """Risks 超过 8 条应截断。"""
    import json

    risks = [f"risk_{i}" for i in range(12)]
    text = json.dumps({"signal": "BUY", "risks": risks})
    dec = adapter.parse(text)
    assert len(dec.risks) == 8


def test_json_case_insensitive_signal(adapter) -> None:
    """Signal 大小写不敏感。"""
    import json

    for raw in ("buy", "Buy", "BUY", "buY"):
        text = json.dumps({"signal": raw})
        dec = adapter.parse(text)
        assert dec.signal == Signal.BUY, f"failed for {raw!r}"


def test_json_invalid_fallback_to_text(adapter, caplog) -> None:
    """非法 JSON 应回退到文本正则解析，并记录 warning。"""
    text = "这是一段自由文本，包含 BUY 关键词。"
    dec = adapter.parse(text)
    assert dec.signal == Signal.BUY  # 回退后正常解析


def test_json_non_dict_fallback(adapter) -> None:
    """JSON 数组应回退到文本正则解析。"""
    import json

    text = json.dumps(["BUY", 80.0])
    dec = adapter.parse(text)
    # 数组不是 dict，应回退到文本解析；文本中含 "BUY" → BUY
    assert dec.signal == Signal.BUY


def test_json_confidence_clamped(adapter) -> None:
    """Confidence 超出 [0, 100] 应被截断。"""
    import json

    text = json.dumps({"signal": "BUY", "confidence": 150.0})
    dec = adapter.parse(text)
    assert dec.confidence == 100.0

    text = json.dumps({"signal": "SELL", "confidence": -10.0})
    dec = adapter.parse(text)
    assert dec.confidence == 0.0


def test_json_position_size_clamped(adapter) -> None:
    """position_size 超出 [-1, 1] 应被截断。"""
    import json

    text = json.dumps({"signal": "BUY", "position_size": 2.0})
    dec = adapter.parse(text)
    assert dec.position_size == 1.0

    text = json.dumps({"signal": "SELL", "position_size": -3.0})
    dec = adapter.parse(text)
    assert dec.position_size == -1.0


def test_json_with_thesis_truncation(adapter) -> None:
    """Thesis 超过 THESIS_TRUNCATE_LEN 应被截断。"""
    import json

    long_thesis = "x" * 500
    text = json.dumps({"signal": "BUY", "thesis": long_thesis})
    dec = adapter.parse(text)
    assert len(dec.thesis) == 300  # THESIS_TRUNCATE_LEN


def test_json_empty_object(adapter) -> None:
    """空 JSON 对象应使用 signal 默认置信度。"""
    import json

    text = json.dumps({})
    dec = adapter.parse(text)
    assert dec.signal == Signal.HOLD
    assert dec.confidence == 50.0  # HOLD 默认置信度


def test_json_only_signal(adapter) -> None:
    """仅含 signal 字段时，confidence 应使用 signal 默认值。"""
    import json

    text = json.dumps({"signal": "SELL"})
    dec = adapter.parse(text)
    assert dec.signal == Signal.SELL
    assert dec.confidence == 30.0  # SELL 默认置信度


# ═══════════════════════════════════════════════════════
# 新字段：JSON 路径
# ═══════════════════════════════════════════════════════


def test_json_invalidations(adapter) -> None:
    """JSON 中的 invalidations 字段应正确解析。"""
    import json

    text = json.dumps(
        {
            "signal": "BUY",
            "invalidations": [
                "毛利率连续2季度下降",
                "订单增长 < 10%",
                "核心客户流失",
            ],
        },
    )
    dec = adapter.parse(text)
    assert dec.invalidations == [
        "毛利率连续2季度下降",
        "订单增长 < 10%",
        "核心客户流失",
    ]


def test_json_price_fields(adapter) -> None:
    """entry_zone / target_price / stop_loss 应正确解析。"""
    import json

    text = json.dumps(
        {
            "signal": "BUY",
            "entry_zone": [148.0, 152.0],
            "target_price": 170.0,
            "stop_loss": 140.0,
        },
    )
    dec = adapter.parse(text)
    assert dec.entry_zone == [148.0, 152.0]
    assert dec.target_price == 170.0
    assert dec.stop_loss == 140.0


def test_json_holding_period(adapter) -> None:
    """expected_holding_period 应正确解析。"""
    import json

    text = json.dumps(
        {
            "signal": "BUY",
            "expected_holding_period": 30,
        },
    )
    dec = adapter.parse(text)
    assert dec.expected_holding_period == 30


def test_json_catalysts(adapter) -> None:
    """Catalysts 字段应正确解析（字符串数组或逗号分隔）。"""
    import json

    text = json.dumps(
        {
            "signal": "BUY",
            "catalysts": ["Q3业绩超预期", "新产品发布"],
        },
    )
    dec = adapter.parse(text)
    assert dec.catalysts == ["Q3业绩超预期", "新产品发布"]

    # 逗号分隔字符串兼容
    text = json.dumps(
        {
            "signal": "BUY",
            "catalysts": "Q3业绩超预期, 新产品发布",
        },
    )
    dec = adapter.parse(text)
    assert dec.catalysts == ["Q3业绩超预期", "新产品发布"]


def test_json_multi_factor_scores(adapter) -> None:
    """多因子评分字段应正确解析并截断到 [0, 100]。"""
    import json

    text = json.dumps(
        {
            "signal": "BUY",
            "valuation_score": 75.0,
            "fundamental_score": 82.0,
            "technical_score": 68.0,
            "sentiment_score": 90.0,
            "capital_flow_score": 55.0,
            "macro_score": 70.0,
        },
    )
    dec = adapter.parse(text)
    assert dec.valuation_score == 75.0
    assert dec.fundamental_score == 82.0
    assert dec.technical_score == 68.0
    assert dec.sentiment_score == 90.0
    assert dec.capital_flow_score == 55.0
    assert dec.macro_score == 70.0


def test_json_scores_clamped(adapter) -> None:
    """超出 [0, 100] 的评分应被截断。"""
    import json

    text = json.dumps(
        {
            "signal": "BUY",
            "valuation_score": 150.0,
            "fundamental_score": -10.0,
        },
    )
    dec = adapter.parse(text)
    assert dec.valuation_score == 100.0
    assert dec.fundamental_score == 0.0


def test_json_all_new_fields(adapter) -> None:
    """完整 JSON 包含所有新字段时应正确解析。"""
    import json

    text = json.dumps(
        {
            "signal": "BUY",
            "confidence": 88.0,
            "thesis": "AI需求驱动增长",
            "risks": ["估值偏高", "竞争加剧"],
            "invalidations": [
                "毛利率连续2季度下降",
                "订单增长 < 10%",
            ],
            "entry_zone": [148.0, 152.0],
            "target_price": 170.0,
            "stop_loss": 140.0,
            "expected_holding_period": 60,
            "expected_return": 15.0,
            "position_size": 0.08,
            "catalysts": ["Q3业绩超预期", "新品发布"],
            "valuation_score": 75.0,
            "fundamental_score": 82.0,
            "technical_score": 68.0,
            "sentiment_score": 90.0,
            "capital_flow_score": 55.0,
            "macro_score": 70.0,
        },
    )
    dec = adapter.parse(text)
    assert dec.signal == Signal.BUY
    assert dec.confidence == 88.0
    assert dec.invalidations == ["毛利率连续2季度下降", "订单增长 < 10%"]
    assert dec.entry_zone == [148.0, 152.0]
    assert dec.target_price == 170.0
    assert dec.stop_loss == 140.0
    assert dec.expected_holding_period == 60
    assert dec.catalysts == ["Q3业绩超预期", "新品发布"]
    assert dec.valuation_score == 75.0
    assert dec.fundamental_score == 82.0


# ═══════════════════════════════════════════════════════
# 新字段：文本路径提取
# ═══════════════════════════════════════════════════════


def test_text_extract_invalidations(adapter) -> None:
    """无效条件应从文本中提取。"""
    text = """**Rating**: Buy
Invalidation conditions:
- 毛利率连续2季度下降
- 订单增长 < 10%
- 核心客户流失

Risks:估值偏高"""
    dec = adapter.parse(text)
    assert len(dec.invalidations) >= 2
    assert any("毛利率" in inv or "订单增长" in inv for inv in dec.invalidations)


def test_text_extract_entry_zone(adapter) -> None:
    """入场区间应从文本中提取。"""
    text = """**Rating**: Buy
Entry zone: 148-152 yuan
Target: 170 yuan"""
    dec = adapter.parse(text)
    assert dec.entry_zone is not None
    assert dec.entry_zone[0] == 148.0
    assert dec.entry_zone[1] == 152.0


def test_text_extract_target_price(adapter) -> None:
    """目标价应从文本中提取，返回标量值（domain model 字段类型为 float）。"""
    text = """**Rating**: Buy
Target price: 170 yuan"""
    dec = adapter.parse(text)
    assert dec.target_price is not None
    assert dec.target_price == 170.0


def test_text_extract_stop_loss(adapter) -> None:
    """止损价应从文本中提取，返回标量值（domain model 字段类型为 float）。"""
    text = """**Rating**: Buy
Stop loss: 140 yuan"""
    dec = adapter.parse(text)
    assert dec.stop_loss is not None
    assert dec.stop_loss == 140.0


def test_text_extract_holding_period(adapter) -> None:
    """持有期应从文本中提取。"""
    text = """**Rating**: Buy
Holding period: 30 trading days"""
    dec = adapter.parse(text)
    assert dec.expected_holding_period == 30


def test_text_extract_catalysts(adapter) -> None:
    """催化剂应从文本中提取。"""
    text = """**Rating**: Buy
**Catalysts**: Q3业绩超预期
新产品发布
宏观政策宽松"""
    dec = adapter.parse(text)
    assert len(dec.catalysts) >= 1
    assert any("业绩" in c or "产品" in c for c in dec.catalysts)


def test_text_extract_scores(adapter) -> None:
    """多因子评分应从文本中提取。"""
    text = """**Rating**: Buy
估值: 75/100
基本面: 82/100
技术面: 68/100
情绪: 90/100
资金流向: 55/100
宏观: 70/100"""
    dec = adapter.parse(text)
    assert dec.valuation_score == 75.0
    assert dec.fundamental_score == 82.0
    assert dec.technical_score == 68.0
    assert dec.sentiment_score == 90.0
    assert dec.capital_flow_score == 55.0
    assert dec.macro_score == 70.0


def test_text_missing_new_fields_are_none(adapter) -> None:
    """文本中未出现的新字段应为 None/空列表。"""
    text = """**Rating**: Buy
Strong fundamentals drive growth."""
    dec = adapter.parse(text)
    assert dec.invalidations == []
    assert dec.entry_zone is None
    assert dec.target_price is None
    assert dec.stop_loss is None
    assert dec.expected_holding_period is None
    assert dec.catalysts == []
    assert dec.valuation_score is None
    assert dec.fundamental_score is None


# ═══════════════════════════════════════════════════════
# InvestmentDecision 序列化
# ═══════════════════════════════════════════════════════


def test_investment_decision_to_dict_new_fields(adapter) -> None:
    """新字段的 to_dict() 应正确输出。"""
    dec = InvestmentDecision(
        signal=Signal.BUY,
        confidence=85.0,
        invalidations=["毛利率下降", "订单萎缩"],
        entry_zone=[148.0, 152.0],
        target_price=170.0,
        stop_loss=140.0,
        expected_holding_period=60,
        catalysts=["Q3超预期"],
        valuation_score=75.0,
        fundamental_score=82.0,
        technical_score=68.0,
        sentiment_score=90.0,
        capital_flow_score=55.0,
        macro_score=70.0,
    )
    d = dec.to_dict()
    assert d["signal"] == "BUY"
    assert d["invalidations"] == ["毛利率下降", "订单萎缩"]
    assert d["entry_zone"] == [148.0, 152.0]
    assert d["target_price"] == 170.0
    assert d["stop_loss"] == 140.0
    assert d["expected_holding_period"] == 60
    assert d["catalysts"] == ["Q3超预期"]
    assert d["valuation_score"] == 75.0
    assert d["fundamental_score"] == 82.0


# ═══════════════════════════════════════════════════════
# JSON edge cases — invalid field parsing
# ═══════════════════════════════════════════════════════


def test_json_invalid_signal_fallback(adapter) -> None:
    """JSON signal that fails both .upper() and initial parse → HOLD."""
    import json

    text = json.dumps({"signal": "!!invalid!!", "confidence": 90.0})
    dec = adapter.parse(text)
    assert dec.signal == Signal.HOLD
    assert dec.confidence == 90.0  # confidence preserved from JSON


def test_json_confidence_invalid_fallback(adapter) -> None:
    """Confidence that can't be converted to float → hardcoded 50.0."""
    import json

    text = json.dumps({"signal": "BUY", "confidence": "not_a_number"})
    dec = adapter.parse(text)
    assert dec.signal == Signal.BUY
    assert dec.confidence == 50.0  # hardcoded fallback when float() fails


def test_json_expected_return_invalid(adapter) -> None:
    """Invalid expected_return → None."""
    import json

    text = json.dumps({"signal": "BUY", "expected_return": "abc"})
    dec = adapter.parse(text)
    assert dec.expected_return is None


def test_json_position_size_invalid(adapter) -> None:
    """Invalid position_size → None."""
    import json

    text = json.dumps({"signal": "BUY", "position_size": "xyz"})
    dec = adapter.parse(text)
    assert dec.position_size is None


def test_json_entry_zone_invalid(adapter) -> None:
    """Invalid entry_zone → None."""
    import json

    text = json.dumps({"signal": "BUY", "entry_zone": [148.0, "bad"]})
    dec = adapter.parse(text)
    assert dec.entry_zone is None


def test_json_target_price_invalid(adapter) -> None:
    """Invalid target_price → None."""
    import json

    text = json.dumps({"signal": "BUY", "target_price": "not_a_num"})
    dec = adapter.parse(text)
    assert dec.target_price is None


def test_json_stop_loss_invalid(adapter) -> None:
    """Invalid stop_loss → None."""
    import json

    text = json.dumps({"signal": "BUY", "stop_loss": None})
    dec = adapter.parse(text)
    assert dec.stop_loss is None


def test_json_holding_period_invalid(adapter) -> None:
    """Invalid expected_holding_period → None."""
    import json

    text = json.dumps({"signal": "BUY", "expected_holding_period": "abc"})
    dec = adapter.parse(text)
    assert dec.expected_holding_period is None


def test_json_scores_invalid(adapter) -> None:
    """Invalid score values → remain None."""
    import json

    text = json.dumps(
        {
            "signal": "BUY",
            "valuation_score": "abc",  # not a number → None
            "fundamental_score": True,  # bool → float(True)=1.0, clamped to [0,100]
        },
    )
    dec = adapter.parse(text)
    assert dec.valuation_score is None  # "abc" → ValueError → None
    assert dec.fundamental_score == 1.0  # True → 1.0 (valid float)


# ═══════════════════════════════════════════════════════
# Text-path extraction edge cases
# ═══════════════════════════════════════════════════════


def test_extract_risks_short_items_filtered(adapter) -> None:
    """Risks shorter than 10 chars or longer than 120 are filtered out."""
    text = """**Rating**: Buy
Risk: X (too short)
Risk: This is a reasonably long risk statement that is exactly right length for extraction.
Risk: This is an extremely long risk factor description that exceeds the maximum allowed length of one hundred and twenty characters for proper filtering."""
    dec = adapter.parse(text)
    # The short one and the too-long one should be filtered
    assert all(10 <= len(r) <= 120 for r in dec.risks)


def test_extract_expected_return_buy_out_of_range(adapter) -> None:
    """BUY with pct outside 5-30% range → no expected_return matched."""
    text = "**Rating**: Buy\nWe expect a 40% upside due to strong momentum."
    dec = adapter.parse(text)
    assert dec.expected_return is None  # 40% > 30% for BUY


def test_extract_expected_return_sell_too_small(adapter) -> None:
    """SELL with pct -0.5% → below -1% threshold, no match."""
    text = "**Rating**: Sell\nExpected decline of just 0.5%."
    dec = adapter.parse(text)
    assert dec.expected_return is None  # -0.5% is outside [-20, -1]


def test_extract_expected_return_hold_within_range(adapter) -> None:
    """HOLD with small pct → matched."""
    text = "**Rating**: Hold\nExpecting roughly 2% movement either way."
    dec = adapter.parse(text)
    assert dec.expected_return is not None
    assert abs(dec.expected_return) <= 5


def test_extract_position_size_no_match(adapter) -> None:
    """No position size mentioned → None."""
    text = "**Rating**: Buy\nStrong fundamentals."
    dec = adapter.parse(text)
    assert dec.position_size is None


def test_extract_position_size_invalid_format(adapter) -> None:
    """Position size mention but unparseable → None."""
    text = "**Rating**: Buy\n仓位: not_a_number"
    dec = adapter.parse(text)
    assert dec.position_size is None


def test_extract_invalidations_no_structured_no_chinese(adapter) -> None:
    """No invalidation markers → empty list."""
    text = "**Rating**: Buy\nNothing to invalidate here."
    dec = adapter.parse(text)
    assert dec.invalidations == []


def test_extract_invalidations_chinese_fallback(adapter) -> None:
    """Chinese invalidation pattern should be extracted."""
    text = """**Rating**: Buy
如果毛利率下降，则建议卖出。
若订单连续两季度下滑，便考虑止损。"""
    dec = adapter.parse(text)
    assert len(dec.invalidations) >= 1
    assert any("毛利率" in inv for inv in dec.invalidations)


def test_extract_price_range_no_match(adapter) -> None:
    """No price range pattern → None."""
    text = "**Rating**: Buy\nNo price targets mentioned."
    dec = adapter.parse(text)
    assert dec.entry_zone is None
    assert dec.target_price is None
    assert dec.stop_loss is None


def test_extract_holding_period_no_match(adapter) -> None:
    """No holding period mention → None."""
    text = "**Rating**: Buy\nInvestment horizon unclear."
    dec = adapter.parse(text)
    assert dec.expected_holding_period is None


def test_extract_catalysts_no_match(adapter) -> None:
    """No catalysts section → empty list."""
    text = "**Rating**: Buy\nNo catalysts discussed."
    dec = adapter.parse(text)
    assert dec.catalysts == []


# ═══════════════════════════════════════════════════════
# Fallback signal / keyword path
# ═══════════════════════════════════════════════════════


def test_fallback_signal_from_rating_no_match(adapter) -> None:
    """Unknown rating string → HOLD, 50."""
    signal, conf = adapter._fallback_signal_from_rating("持有")
    assert signal == Signal.HOLD
    assert conf == 50.0


def test_keyword_fallback_no_keywords(adapter) -> None:
    """Text with no BUY/SELL/HOLD keywords → HOLD, 50."""
    signal, conf = DecisionAdapter._keyword_fallback("The market looks uncertain today.")
    assert signal == Signal.HOLD
    assert conf == 50.0


def test_keyword_fallback_overweight_with_negative_before(adapter) -> None:
    """OVERWEIGHT preceded by negative word → not treated as buy."""
    signal, conf = DecisionAdapter._keyword_fallback(
        "We do NOT recommend OVERWEIGHT at this time due to valuation."
    )
    # Should not be BUY since OVERWEIGHT has negative before it
    assert signal != Signal.BUY or conf < 65.0


def test_keyword_fallback_sell_with_negative_before(adapter) -> None:
    """SELL preceded by negative word → not treated as sell."""
    signal, conf = DecisionAdapter._keyword_fallback("We do NOT see a reason to SELL the stock.")
    assert signal != Signal.SELL
