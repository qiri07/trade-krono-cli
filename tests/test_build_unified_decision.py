"""Tests for unified_decision.build_unified_decision factory function.

覆盖：
  · build_unified_decision 构造正确的 UnifiedInvestmentDecision
  · 各可选参数（ta_decision / kronos / committee）组合行为
  · 序列化/反序列化 roundtrip
  · to_ta_decision 转换
  · _direction_to_signal 边界情况
"""
from __future__ import annotations

import pytest

from trade_krono_cli.ta_decision import InvestmentDecision as TADecision
from trade_krono_cli.ta_decision import Signal
from trade_krono_cli.unified_decision import (
    _direction_to_signal,
    build_unified_decision,
)

# ═══════════════════════════════════════════════════════
#  _direction_to_signal
# ═══════════════════════════════════════════════════════


class TestDirectionToSignal:
    def test_up(self) -> None:
        assert _direction_to_signal("UP") is Signal.BUY

    def test_down(self) -> None:
        assert _direction_to_signal("DOWN") is Signal.SELL

    def test_flat(self) -> None:
        assert _direction_to_signal("FLAT") is Signal.HOLD

    def test_none(self) -> None:
        assert _direction_to_signal(None) is None

    def test_lowercase(self) -> None:
        assert _direction_to_signal("up") is Signal.BUY
        assert _direction_to_signal("down") is Signal.SELL
        assert _direction_to_signal("flat") is Signal.HOLD

    def test_unknown_returns_none(self) -> None:
        assert _direction_to_signal("MIDDLE") is None


# ═══════════════════════════════════════════════════════
#  build_unified_decision
# ═══════════════════════════════════════════════════════


class TestBuildUnifiedDecision:
    """build_unified_decision 工厂函数测试。"""

    def test_minimal(self) -> None:
        """仅 ticker/date 应产生默认 HOLD 决策。"""
        d = build_unified_decision("sh.600519", "2026-08-11")
        assert d.ticker == "sh.600519"
        assert d.eval_date == "2026-08-11"
        assert d.final_signal is Signal.HOLD
        assert d.final_confidence == pytest.approx(50.0, abs=1)
        assert d.ta_signal is None
        assert d.kronos_direction is None
        assert d.committee_rec is None
        assert d.conflict == "none"

    def test_with_ta_only(self) -> None:
        """仅 TA 源：信号应来自 TA。"""
        ta = TADecision(
            signal=Signal.BUY,
            confidence=80.0,
            thesis="基本面良好",
            risks=["估值偏高"],
            invalidations=["PE回落至20以下"],
        )
        d = build_unified_decision("sh.600519", "2026-08-11", ta_decision=ta)
        assert d.ta_signal is Signal.BUY
        assert d.ta_confidence == 80.0
        assert d.ta_reasoning == "基本面良好"
        assert d.final_signal is Signal.BUY
        assert d.thesis == "基本面良好"
        assert d.risks == ["估值偏高"]
        assert d.invalidations == ["PE回落至20以下"]

    def test_with_kronos_only(self) -> None:
        """仅 Kronos 源：方向应映射为信号。"""
        d = build_unified_decision(
            "sz.000858",
            "2026-08-11",
            kronos_direction="UP",
            kronos_expected_return=3.5,
            distribution={"direction_score": 0.8, "p10": 24.0, "p90": 27.0},
        )
        assert d.kronos_direction == "UP"
        assert d.kronos_expected_return == 3.5
        assert d.direction_score == 0.8
        assert d.p10 == 24.0
        assert d.p90 == 27.0
        assert d.final_signal is Signal.BUY  # UP → BUY

    def test_with_committee_only(self) -> None:
        """仅委员会源：推荐信号应反映在最终决策中。"""
        d = build_unified_decision(
            "sh.600036",
            "2026-08-11",
            committee_rec=Signal.SELL,
            committee_confidence=75.0,
            bull_case="行业龙头",
            bear_case="政策风险",
        )
        assert d.committee_rec is Signal.SELL
        assert d.committee_confidence == 75.0
        assert d.bull_case == "行业龙头"
        assert d.bear_case == "政策风险"

    def test_full_pipeline(self) -> None:
        """三路全量输入应正确融合。"""
        ta = TADecision(
            signal=Signal.BUY,
            confidence=80.0,
            thesis="趋势向上",
            risks=["波动率偏高"],
        )
        d = build_unified_decision(
            "sh.600519",
            "2026-08-11",
            ta_decision=ta,
            kronos_direction="UP",
            kronos_expected_return=2.5,
            distribution={"direction_score": 0.75, "p10": 1750.0, "p50": 1800.0, "p90": 1850.0},
            committee_rec=Signal.BUY,
            committee_confidence=70.0,
            bull_case="稳健增长",
            bear_case="经济下行",
        )
        assert d.ta_signal is Signal.BUY
        assert d.kronos_direction == "UP"
        assert d.committee_rec is Signal.BUY
        assert d.p10 == 1750.0
        assert d.p50 == 1800.0
        assert d.p90 == 1850.0
        assert d.bull_case == "稳健增长"
        assert d.bear_case == "经济下行"

    def test_empty_distribution(self) -> None:
        """distribution=None 时 p10/p90 等应为 None。"""
        d = build_unified_decision(
            "sh.600519",
            "2026-08-11",
            kronos_direction="UP",
            distribution=None,
        )
        assert d.p10 is None
        assert d.p90 is None
        assert d.direction_score is None

    def test_empty_dict_distribution(self) -> None:
        """distribution={} 时所有分位数为 None。"""
        d = build_unified_decision(
            "sh.600519",
            "2026-08-11",
            kronos_direction="UP",
            distribution={},
        )
        assert d.p10 is None
        assert d.p25 is None
        assert d.p50 is None
        assert d.p75 is None
        assert d.p90 is None
        assert d.direction_score is None

    def test_ta_reasoning_truncated(self) -> None:
        """thesis 超过 200 字符时应截断。"""
        long_thesis = "A" * 300
        ta = TADecision(
            signal=Signal.HOLD,
            confidence=50.0,
            thesis=long_thesis,
        )
        d = build_unified_decision("sh.600519", "2026-08-11", ta_decision=ta)
        assert len(d.ta_reasoning) <= 200
        assert d.ta_reasoning == long_thesis[:200]

    def test_to_dict_roundtrip(self) -> None:
        """序列化/反序列化应保持所有字段。"""
        ta = TADecision(signal=Signal.BUY, confidence=80.0, thesis="test")
        d = build_unified_decision(
            "sh.600519",
            "2026-08-11",
            ta_decision=ta,
            kronos_direction="UP",
            kronos_expected_return=2.0,
            distribution={"p10": 95.0, "p90": 105.0},
            committee_rec=Signal.HOLD,
            committee_confidence=60.0,
            bull_case="bull",
            bear_case="bear",
        )
        data = d.to_dict()
        d2 = UnifiedInvestmentDecision.from_dict(data)
        assert d2.ticker == "sh.600519"
        assert d2.eval_date == "2026-08-11"
        assert d2.ta_signal is Signal.BUY
        assert d2.kronos_direction == "UP"
        assert d2.committee_rec is Signal.HOLD
        assert d2.p10 == 95.0
        assert d2.bull_case == "bull"
        assert d2.bear_case == "bear"


# 导入 UnifiedInvestmentDecision 用于 roundtrip 测试
from trade_krono_cli.unified_decision import UnifiedInvestmentDecision  # noqa: E402
