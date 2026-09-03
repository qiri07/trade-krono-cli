"""ta_decision — TradingAgents 决策标准化适配器。

LLM 输出（结构化 JSON 或自由文本）→ DecisionAdapter → 结构化 InvestmentDecision

解析优先级：
  1. JSON 结构化输出（主动约束格式，准确率最高）
  2. Rating → 关键词 → fallback（自由文本，兼容旧版 prompt）

向后兼容：所有符号从 adapter_impl 重新导出。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

# ── Signal：来自领域层，消除重复定义 ──────────────────────────────────────
from trade_krono_cli.domain.types import Signal

__all__ = ("DecisionAdapter", "InvestmentDecision", "Signal")


# Truncation length for thesis/summary extraction
THESIS_TRUNCATE_LEN = 300


# ── Rating 字符串 → (Signal, base_confidence) ─────────────────────────────────
_RATING_MAP: dict[str, tuple[Signal, float]] = {
    "strong buy": (Signal.BUY, 95.0),
    "buy": (Signal.BUY, 80.0),
    "overweight": (Signal.OVERWEIGHT, 70.0),
    "neutral": (Signal.HOLD, 50.0),
    "hold": (Signal.HOLD, 50.0),
    "underweight": (Signal.SELL, 40.0),
    "sell": (Signal.SELL, 30.0),
    "strong sell": (Signal.SELL, 15.0),
}

# 否定词集合（出现在目标词前 N 个词内即视为否定）
_NEG_WORDS = frozenset(
    {
        "NOT",
        "NO",
        "NEVER",
        "FAIL",
        "FAILS",
        "FAILED",
        "NEITHER",
        "NON",
        "UNLIKELY",
        "NEGATIVE",
    },
)


# ═══════════════════════════════════════════════════════
# InvestmentDecision — 扩展结构
# ═══════════════════════════════════════════════════════


@dataclass
class InvestmentDecision:
    """TradingAgents 决策的标准化结构。

    基础字段
    ──────────────────────────────────────────────────────
    signal            买入 / 持有 / 卖出
    confidence        0–100，基于 Rating 强度 + 辅助佐证微调
    expected_return   预期收益率（%），从文本解析，未找到则为 None
    position_size     建议仓位比例，-1~1，未找到则为 None
    horizon           投资周期（交易日），未找到则为 None

    投资框架字段
    ──────────────────────────────────────────────────────
    thesis            核心投资论点（Executive Summary 或 Thesis 段落摘要）
    risks             风险清单（从文本中提取）
    invalidations     投资逻辑失效条件列表（回测关键）

    交易执行字段
    ──────────────────────────────────────────────────────
    entry_zone        建议入场价区间，如 [148.0, 152.0]
    target_price      目标价（元），未找到则为 None
    stop_loss         止损价（元），未找到则为 None
    expected_holding_period  预期持有天数，未找到则为 None

    多因子评分
    ──────────────────────────────────────────────────────
    valuation_score         估值评分（0–100）
    fundamental_score       基本面评分（0–100）
    technical_score         技术面评分（0–100）
    sentiment_score         情绪面评分（0–100）
    capital_flow_score      资金流向评分（0–100）
    macro_score             宏观评分（0–100）

    催化剂
    ──────────────────────────────────────────────────────
    catalysts             潜在催化剂列表
    """

    # 基础字段
    signal: Signal
    confidence: float
    expected_return: float | None = None
    position_size: float | None = None
    horizon: int | None = None

    # 投资框架
    thesis: str = ""
    risks: list[str] = field(default_factory=list)
    invalidations: list[str] = field(default_factory=list)

    # 交易执行
    entry_zone: list[float] | None = None
    target_price: float | None = None
    stop_loss: float | None = None
    expected_holding_period: int | None = None

    # 多因子评分
    valuation_score: float | None = None
    fundamental_score: float | None = None
    technical_score: float | None = None
    sentiment_score: float | None = None
    capital_flow_score: float | None = None
    macro_score: float | None = None

    # 催化剂
    catalysts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["signal"] = self.signal.value
        return d

    @classmethod
    def from_dict(cls, data: dict) -> InvestmentDecision:
        """从 dict 反序列化，将 signal 字符串还原为 Signal 枚举。"""
        signal_str = data.get("signal", "HOLD")
        if isinstance(signal_str, str):
            try:
                signal = Signal(signal_str)
            except ValueError:
                from loguru import logger

                logger.warning(f"未知 Signal 值 '{signal_str}'，回退到 HOLD")
                signal = Signal.HOLD
            data = dict(data)
            data["signal"] = signal
        return cls(**data)

    @classmethod
    def fallback(
        cls, signal: Signal = Signal.HOLD, confidence: float = 50.0,
    ) -> InvestmentDecision:
        return cls(signal=signal, confidence=confidence)


# ═══════════════════════════════════════════════════════
# DecisionAdapter — 从 adapter_impl 导入
# ═══════════════════════════════════════════════════════

from trade_krono_cli.ta_decision.adapter_impl import DecisionAdapter  # noqa: E402

__all__ = ("DecisionAdapter", "InvestmentDecision", "Signal")
