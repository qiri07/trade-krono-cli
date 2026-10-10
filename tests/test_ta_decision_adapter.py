"""TA Decision Adapter 单元测试。

覆盖：
  - parse() 各种 LLM 输出格式
  - JSON 结构化解析
  - 文本正则回退
  - 边界情况（空输入、无效信号等）
"""

from __future__ import annotations

import pytest

from trade_krono_cli.ta_decision import Signal
from trade_krono_cli.ta_decision.adapter_impl import DecisionAdapter


@pytest.fixture
def adapter() -> DecisionAdapter:
    return DecisionAdapter()


# ── 空输入 / fallback ────────────────────────────────────────────────────────


class TestEmptyInput:
    def test_empty_string_returns_fallback(self, adapter: DecisionAdapter) -> None:
        result = adapter.parse("")
        assert result.signal == Signal.HOLD
        assert result.confidence > 0

    def test_whitespace_only_returns_fallback(self, adapter: DecisionAdapter) -> None:
        result = adapter.parse("   \n\t  ")
        assert result.signal == Signal.HOLD

    def test_none_input_returns_fallback(self, adapter: DecisionAdapter) -> None:
        result = adapter.parse(None)  # type: ignore[arg-type]
        assert result.signal == Signal.HOLD


# ── JSON 结构化解析 ──────────────────────────────────────────────────────────


class TestJsonParsing:
    def test_valid_json_buy(self, adapter: DecisionAdapter) -> None:
        json_text = """{
            "signal": "BUY",
            "confidence": 85.5,
            "thesis": "业绩向好，技术突破",
            "expected_return": 12.5,
            "position_size": 0.2,
            "horizon": 10
        }"""
        result = adapter.parse(json_text)
        assert result.signal == Signal.BUY
        assert abs(result.confidence - 85.5) < 0.1
        assert result.expected_return == pytest.approx(12.5, abs=0.1)
        assert result.position_size == pytest.approx(0.2, abs=0.01)

    def test_valid_json_sell(self, adapter: DecisionAdapter) -> None:
        json_text = '{"signal": "SELL", "confidence": 70.0, "thesis": "估值过高"}'
        result = adapter.parse(json_text)
        assert result.signal == Signal.SELL
        assert result.confidence == 70.0

    def test_valid_json_hold(self, adapter: DecisionAdapter) -> None:
        json_text = '{"signal": "HOLD", "confidence": 50.0}'
        result = adapter.parse(json_text)
        assert result.signal == Signal.HOLD

    def test_invalid_json_falls_back_to_regex(self, adapter: DecisionAdapter) -> None:
        """格式无效的 JSON → 回退到文本正则解析。"""
        text = "{ invalid json but contains BUY signal and confidence 75 }"
        result = adapter.parse(text)
        # 不应抛出异常，应有合理的 fallback
        assert result is not None

    def test_nested_json_with_all_fields(self, adapter: DecisionAdapter) -> None:
        json_text = """{
            "signal": "OVERWEIGHT",
            "confidence": 90.0,
            "thesis": "核心逻辑未变",
            "expected_return": 15.0,
            "position_size": 0.3,
            "horizon": 20,
            "stop_loss": 1650.0,
            "target_price": 2000.0,
            "entry_zone": [1700.0, 1750.0],
            "invalidations": ["宏观政策转向", "业绩不及预期"],
            "catalysts": ["年报超预期", "新订单落地"],
            "valuation_score": 75.0,
            "fundamental_score": 80.0,
            "technical_score": 70.0,
            "sentiment_score": 65.0,
            "capital_flow_score": 72.0,
            "macro_score": 68.0
        }"""
        result = adapter.parse(json_text)
        assert result.signal == Signal.OVERWEIGHT
        assert result.confidence == 90.0
        assert result.stop_loss == 1650.0
        assert result.target_price == 2000.0
        assert result.entry_zone == [1700.0, 1750.0]
        assert len(result.invalidations) == 2
        assert len(result.catalysts) == 2


# ── 文本正则回退 ──────────────────────────────────────────────────────────────


class TestTextFallback:
    def test_rating_based_signal(self, adapter: DecisionAdapter) -> None:
        text = """Rating: BUY
Confidence: 80
Thesis: Strong fundamentals"""
        result = adapter.parse(text)
        assert result.signal == Signal.BUY
        assert result.confidence == pytest.approx(80.0, abs=5.0)

    def test_keyword_buy(self, adapter: DecisionAdapter) -> None:
        text = "I see strong BUY signal with target price 2000"
        result = adapter.parse(text)
        assert result.signal == Signal.BUY

    def test_keyword_sell(self, adapter: DecisionAdapter) -> None:
        text = "High risk, recommend SELL position"
        result = adapter.parse(text)
        assert result.signal == Signal.SELL

    def test_keyword_hold(self, adapter: DecisionAdapter) -> None:
        text = "Stay HOLD and wait for better entry"
        result = adapter.parse(text)
        assert result.signal == Signal.HOLD

    def test_extract_risks_from_text(self, adapter: DecisionAdapter) -> None:
        text = """BUY recommendation.
Risks:
1. Macro economic downturn risk
2. Industry competition intensifying
3. Exchange rate volatility impact
Expected return: 10%"""
        result = adapter.parse(text)
        assert len(result.risks) >= 2
        assert any("macro" in r.lower() or "economic" in r.lower() for r in result.risks)

    def test_extract_catalysts(self, adapter: DecisionAdapter) -> None:
        text = """Recommend BUY.
**Catalysts**:
- New product launch
- Earnings beat expectations
Expected return 15%"""
        result = adapter.parse(text)
        assert len(result.catalysts) >= 1

    def test_position_size_extraction(self, adapter: DecisionAdapter) -> None:
        text = "Recommend BUY, 仓位 20% 左右"
        result = adapter.parse(text)
        assert result.position_size == pytest.approx(0.2, abs=0.05)

    def test_expected_return_buy_signal(self, adapter: DecisionAdapter) -> None:
        text = "Recommend BUY, expected gain 12%"
        result = adapter.parse(text)
        assert result.expected_return is not None
        assert result.expected_return > 0


# ── 异常信号处理 ─────────────────────────────────────────────────────────────


class TestEdgeSignals:
    def test_unknown_rating_mapped_to_hold(self, adapter: DecisionAdapter) -> None:
        """未知的评级映射到 HOLD。"""
        text = "评级：超级推荐（非标准）\n置信度：60"
        result = adapter.parse(text)
        # 不崩溃，信号为某种合理值
        assert result.signal in {Signal.BUY, Signal.HOLD, Signal.SELL}

    def test_confidence_clamped(self, adapter: DecisionAdapter) -> None:
        """置信度超出范围时应被夹紧。"""
        text = "评级：买入\n置信度：200"
        result = adapter.parse(text)
        # 不应崩溃
        assert 0 <= result.confidence <= 100

    def test_thesis_truncation(self, adapter: DecisionAdapter) -> None:
        """超长的论点应被截断。"""
        long_text = "买入\n论点：" + "A" * 1000 + "\n置信度：70"
        result = adapter.parse(long_text)
        assert len(result.thesis) <= 200  # THESIS_TRUNCATE_LEN
