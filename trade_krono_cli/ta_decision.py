"""
InvestmentDecision — TradingAgents 决策标准化适配器。

LLM 输出（自由文本）→ DecisionAdapter → 结构化 InvestmentDecision
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict, field
from enum import Enum
from typing import Optional


class Signal(str, Enum):
    BUY = "BUY"
    HOLD = "HOLD"
    SELL = "SELL"


# ── Rating 字符串 → (Signal, base_confidence) ─────────────────────────────────
_RATING_MAP: dict[str, tuple[Signal, float]] = {
    "strong buy":       (Signal.BUY,  95.0),
    "buy":              (Signal.BUY,  80.0),
    "overweight":       (Signal.BUY,  70.0),
    "neutral":          (Signal.HOLD, 50.0),
    "hold":             (Signal.HOLD, 50.0),
    "underweight":      (Signal.SELL, 40.0),
    "sell":             (Signal.SELL, 30.0),
    "strong sell":      (Signal.SELL, 15.0),
}

# 否定词集合（出现在目标词前 N 个词内即视为否定）
_NEG_WORDS = frozenset({
    "NOT", "NO", "NEVER", "FAIL", "FAILS", "FAILED", "NEITHER",
    "NON", "UNLIKELY", "NEGATIVE",
})


@dataclass
class InvestmentDecision:
    """
    TradingAgents 决策的标准化结构。

    signal            买入 / 持有 / 卖出
    confidence        0–100，基于 Rating 强度 + 辅助佐证微调
    expected_return   预期收益率（%），从文本解析，未找到则为 None
    position_size     建议仓位比例，-1~1，未找到则为 None
    horizon           投资周期（交易日），未找到则为 None
    thesis            核心投资论点（Executive Summary 或 Thesis 段落摘要）
    risks             风险清单（从文本中提取）
    """
    signal: Signal
    confidence: float
    expected_return: Optional[float] = None
    position_size: Optional[float] = None
    horizon: Optional[int] = None
    thesis: str = ""
    risks: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["signal"] = self.signal.value
        return d

    @classmethod
    def fallback(cls, signal: Signal = Signal.HOLD, confidence: float = 50.0) -> "InvestmentDecision":
        return cls(signal=signal, confidence=confidence)


# ── DecisionAdapter ───────────────────────────────────────────────────────────

class DecisionAdapter:
    """
    将 TradingAgents 自由文本输出解析为结构化 InvestmentDecision。

    解析优先级：
      1. **Rating**: <value> 结构化字段 → 信号 + 基础置信度
      2. **Investment Thesis** / **Executive Summary** → thesis 摘要
      3. 百分比数字模式 → expected_return（如有）
      4. 风险关键词 + 列表格式 → risks
      5. keyword fallback（负上下文感知）→ 兜底信号
      6. fallback → HOLD, 50
    """

    # Rating 正则：匹配 **Rating**: Underweight 或 Rating: BUY 等
    _RE_RATING = re.compile(
        r"\*\*Rating\*\*\s*[:：]\s*([A-Za-z]+(?:\s+[A-Za-z]+)?)",
        re.IGNORECASE,
    )
    # Thesis 段落
    _RE_THESIS = re.compile(
        r"\*\*Investment Thesis\*\*\s*[:：]\s*(.+?)(?=\n\*\*|\Z)",
        re.DOTALL | re.IGNORECASE,
    )
    # Executive Summary（取第一段作为 thesis 补充）
    _RE_SUMMARY = re.compile(
        r"\*\*Executive Summary\*\*\s*[:：]\s*(.+?)(?=\n\*\*|\Z)",
        re.DOTALL | re.IGNORECASE,
    )
    # 百分比数字
    _RE_PCT = re.compile(r"(?<![\d./])(\d+(?:\.\d+)?)\s*%")
    # 持仓比例
    _RE_POS_SIZE = re.compile(r"仓位[:：]?\s*(\d+(?:\.\d+)?)\s*[%‰]?\s*(?:以上|左右|)")

    def parse(self, decision_text: str) -> InvestmentDecision:
        """主入口：解析 LLM 输出文本 → InvestmentDecision。"""
        if not decision_text or not decision_text.strip():
            return InvestmentDecision.fallback()

        # ── 1. Rating 结构化解析 ────────────────────────────────────────────
        rating_match = self._RE_RATING.search(decision_text)
        signal: Signal
        confidence: float

        if rating_match:
            rating_str = rating_match.group(1).strip().lower()
            mapped = _RATING_MAP.get(rating_str)
            if mapped:
                signal, base_conf = mapped
            else:
                signal, base_conf = self._fallback_signal_from_rating(rating_str)
        else:
            # ── 1b. Keyword fallback（负上下文感知）────────────────────────
            signal, base_conf = self._keyword_fallback(decision_text)

        confidence = base_conf

        # ── 2. Thesis 提取 ─────────────────────────────────────────────────
        thesis = self._extract_thesis(decision_text)

        # ── 3. Risks 提取 ──────────────────────────────────────────────────
        risks = self._extract_risks(decision_text)

        # ── 4. Expected return ─────────────────────────────────────────────
        expected_return = self._extract_expected_return(decision_text, signal)

        # ── 5. Position size ───────────────────────────────────────────────
        position_size = self._extract_position_size(decision_text)

        return InvestmentDecision(
            signal=signal,
            confidence=round(confidence, 1),
            expected_return=expected_return,
            position_size=position_size,
            horizon=None,
            thesis=thesis,
            risks=risks,
        )

    # ── 子解析方法 ──────────────────────────────────────────────────────────

    def _extract_thesis(self, text: str) -> str:
        """提取 Investment Thesis 或 Executive Summary 作为 thesis。"""
        m = self._RE_THESIS.search(text)
        if m:
            return m.group(1).strip()[:300]
        m = self._RE_SUMMARY.search(text)
        if m:
            return m.group(1).strip()[:300]
        # fallback: 取第一句话
        first_sentence = re.split(r"[。！？\n]", text.strip())
        return first_sentence[0][:200] if first_sentence else ""

    def _extract_risks(self, text: str) -> list[str]:
        """从文本中提取风险点列表。"""
        risks: list[str] = []
        # 找风险上下文位置（中英文）
        risk_marker_pos = -1
        for kw in ["风险", "风险点", "担忧", "压力", "隐患", "不利因素", "risks", "Risks", " Risks ", " risk "]:
            pos = text.find(kw)
            if pos >= 0 and (risk_marker_pos < 0 or pos < risk_marker_pos):
                risk_marker_pos = pos

        if risk_marker_pos >= 0:
            chunk = text[risk_marker_pos : risk_marker_pos + 800]
            for line in chunk.split("\n"):
                line = line.strip()
                if not line:
                    continue
                # 去除 bullet 标记（支持 "- "、"* "、"• "、数字列表）
                stripped = re.sub(r"^[\s]*[-•*]?\s*\d+[.）)]?\s*", "", line)
                stripped = re.sub(r"^[\s]*[-•*]\s*", "", stripped)
                stripped = stripped.strip()
                if 10 <= len(stripped) <= 120:
                    if stripped not in risks and len(risks) < 8:
                        risks.append(stripped)
        return risks

    def _extract_expected_return(
        self, text: str, signal: Signal
    ) -> Optional[float]:
        """从文本中提取预期收益率。排除 PE/PEG/股息率等财务比率行。"""
        # 用单词边界匹配英文财务比率，避免 "pe" 误匹配 "expect" 等词
        _FIN_RATIO_RE = re.compile(r'\b(pe|peg|pb|eps|roe|roa)\b', re.IGNORECASE)
        _FIN_RATIO_CN = frozenset({"股息率", "毛利率"})
        for line in text.split("\n"):
            line_lower = line.lower()
            if _FIN_RATIO_RE.search(line) or any(fr in line_lower for fr in _FIN_RATIO_CN):
                continue
            for m in self._RE_PCT.finditer(line):
                pct = float(m.group(1))
                if -30 <= pct <= 50 and pct != 0:
                    if signal == Signal.BUY and 5 <= pct <= 30:
                        return pct
                    if signal == Signal.SELL and -20 <= pct <= -1:
                        return -pct
                    if signal == Signal.HOLD and abs(pct) <= 5:
                        return round(pct, 2)
        return None

    def _extract_position_size(self, text: str) -> Optional[float]:
        """从文本中提取建议仓位比例。"""
        m = self._RE_POS_SIZE.search(text)
        if m:
            try:
                return float(m.group(1)) / 100.0
            except (ValueError, ZeroDivisionError):
                pass
        return None

    def _fallback_signal_from_rating(self, rating_str: str) -> tuple[Signal, float]:
        """未命中预定义映射时的兜底信号解析。"""
        s = rating_str.lower()
        if any(k in s for k in ("buy", "overweight", "strong")):
            return Signal.BUY, 70.0
        if any(k in s for k in ("sell", "underweight")):
            return Signal.SELL, 35.0
        return Signal.HOLD, 50.0

    @staticmethod
    def _has_negative_before(words: list[str], target: str, window: int = 10) -> bool:
        """target 词前 window 个词内是否有否定词。"""
        upper = [w.upper() for w in words]
        idx = None
        for i, w in enumerate(upper):
            if target in w:
                idx = i
                break
        if idx is None:
            return False
        start = max(0, idx - window)
        return bool(set(upper[start:idx]) & _NEG_WORDS)

    @classmethod
    def _keyword_fallback(cls, text: str) -> tuple[Signal, float]:
        """无 Rating 字段时，负上下文感知的关键词兜底。"""
        words = text.split()
        upper_words = [w.upper() for w in words]

        has_buy = any("BUY" in w for w in upper_words)
        if has_buy and not cls._has_negative_before(words, "BUY"):
            return Signal.BUY, 75.0

        has_overweight = any("OVERWEIGHT" in w for w in upper_words)
        if has_overweight and not cls._has_negative_before(words, "OVERWEIGHT"):
            return Signal.BUY, 65.0

        has_sell = any("SELL" in w for w in upper_words)
        if has_sell and not cls._has_negative_before(words, "SELL"):
            return Signal.SELL, 30.0

        has_underweight = any("UNDERWEIGHT" in w for w in upper_words)
        if has_underweight and not cls._has_negative_before(words, "UNDERWEIGHT"):
            return Signal.SELL, 40.0

        has_hold = any("HOLD" in w or "NEUTRAL" in w for w in upper_words)
        if has_hold and not cls._has_negative_before(words, "HOLD"):
            return Signal.HOLD, 50.0

        return Signal.HOLD, 50.0
