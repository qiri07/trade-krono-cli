"""
InvestmentDecision — TradingAgents 决策标准化适配器。

LLM 输出（结构化 JSON 或自由文本）→ DecisionAdapter → 结构化 InvestmentDecision

解析优先级：
  1. JSON 结构化输出（主动约束格式，准确率最高）
  2. Rating → 关键词 → fallback（自由文本，兼容旧版 prompt）
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Optional


class Signal(str, Enum):
    BUY = "BUY"
    OVERWEIGHT = "OVERWEIGHT"
    HOLD = "HOLD"
    SELL = "SELL"


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
    }
)


# ═══════════════════════════════════════════════════════
# InvestmentDecision — 扩展结构
# ═══════════════════════════════════════════════════════


@dataclass
class InvestmentDecision:
    """
    TradingAgents 决策的标准化结构。

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
    expected_return: Optional[float] = None
    position_size: Optional[float] = None
    horizon: Optional[int] = None

    # 投资框架
    thesis: str = ""
    risks: list[str] = field(default_factory=list)
    invalidations: list[str] = field(default_factory=list)

    # 交易执行
    entry_zone: Optional[list[float]] = None
    target_price: Optional[float] = None
    stop_loss: Optional[float] = None
    expected_holding_period: Optional[int] = None

    # 多因子评分
    valuation_score: Optional[float] = None
    fundamental_score: Optional[float] = None
    technical_score: Optional[float] = None
    sentiment_score: Optional[float] = None
    capital_flow_score: Optional[float] = None
    macro_score: Optional[float] = None

    # 催化剂
    catalysts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["signal"] = self.signal.value
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "InvestmentDecision":
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
        cls, signal: Signal = Signal.HOLD, confidence: float = 50.0
    ) -> "InvestmentDecision":
        return cls(signal=signal, confidence=confidence)


# ═══════════════════════════════════════════════════════
# DecisionAdapter
# ═══════════════════════════════════════════════════════


class DecisionAdapter:
    """
    将 TradingAgents 输出解析为结构化 InvestmentDecision。

    解析优先级：
      1. JSON 结构化输出 → 直接映射字段
      2. **Rating**: <value> → 信号 + 基础置信度
      3. **Investment Thesis** / **Executive Summary** → thesis
      4. 百分比数字模式 → expected_return
      5. keyword fallback（负上下文感知）→ 兜底信号
      6. fallback → HOLD, 50
    """

    # Rating 正则
    _RE_RATING = re.compile(
        r"\*\*Rating\*\*\s*[:：]\s*([A-Za-z]+(?:\s+[A-Za-z]+)?)",
        re.IGNORECASE,
    )
    # Thesis 段落
    _RE_THESIS = re.compile(
        r"\*\*Investment Thesis\*\*\s*[:：]\s*(.+?)(?=\n\*\*|\Z)",
        re.DOTALL | re.IGNORECASE,
    )
    # Executive Summary
    _RE_SUMMARY = re.compile(
        r"\*\*Executive Summary\*\*\s*[:：]\s*(.+?)(?=\n\*\*|\Z)",
        re.DOTALL | re.IGNORECASE,
    )
    # 百分比数字
    _RE_PCT = re.compile(r"(?<![\d./])(\d+(?:\.\d+)?)\s*%")
    # 持仓比例
    _RE_POS_SIZE = re.compile(r"仓位[:：]?\s*(\d+(?:\.\d+)?)\s*[%‰]?\s*(?:以上|左右|)")

    # 止损相关
    _RE_STOP_LOSS = re.compile(
        r"(?:止损|stop\s*loss)[:：]?\s*([\d.,]+\s*[-–—至到]\s*[\d.,]+|[≥≤><=]?\s*[\d.,]+)",
        re.IGNORECASE,
    )
    _RE_TARGET_PRICE = re.compile(
        r"(?:目标价|target\s*(?:price|price\s*target)|目标)[:：]?\s*([\d.,]+\s*[-–—至到]\s*[\d.,]+|[≥≤><=]?\s*[\d.,]+)",
        re.IGNORECASE,
    )
    _RE_ENTRY_ZONE = re.compile(
        r"(?:入场区?间|entry\s*(?:zone|price)|建议买入)[:：]?\s*([\d.,]+\s*[-–—至到]\s*[\d.,]+|[≥≤><=]?\s*[\d.,]+)",
        re.IGNORECASE,
    )

    # 持有期（更宽松的匹配）
    _RE_HOLDING_PERIOD = re.compile(
        r"(?:持有期|holding\s*period|预期持有)[:：]?\s*(\d+)",
        re.IGNORECASE,
    )

    # 失效条件
    _RE_INVALIDATIONS = re.compile(
        r"(?:失效条件|invalidation|if.*?则?卖出|逻辑失效)[:：]?\s*(.+?)(?=\n\s*(?:风险|catalyst|\*\*|#####)|\Z)",
        re.DOTALL | re.IGNORECASE,
    )

    # 催化剂
    _RE_CATALYSTS = re.compile(
        r"\*\*Catalysts\*\*\s*[:：]\s*(.+?)(?=\n\*\*|\Z)",
        re.DOTALL | re.IGNORECASE,
    )

    # 多因子评分
    _RE_SCORE = re.compile(
        r"(?:估值|基本面|技术面|情绪|资金流向|宏观)[:]?\s*(\d+(?:\.\d+)?)\s*/?\s*100",
        re.IGNORECASE,
    )

    def parse(self, decision_text: str) -> InvestmentDecision:
        """
        主入口：解析 LLM 输出 → InvestmentDecision。

        优先尝试 JSON 结构化解析，失败后回退到自由文本正则解析。
        """
        from loguru import logger

        if not decision_text or not decision_text.strip():
            return InvestmentDecision.fallback()

        # ── 0. JSON 结构化解析（优先路径）────────────────────────────────────
        decision_text_stripped = decision_text.strip()
        json_decision = self._try_parse_json(decision_text_stripped)
        if json_decision is not None:
            return json_decision

        # ── JSON 解析失败，回退到文本正则解析 ────────────────────────────────
        logger.warning(
            f"[TA决策解析] JSON 结构化解析失败，回退到文本正则解析。"
            f"请检查 LLM prompt 是否要求返回标准 JSON 格式。\n"
            f"  原始输出前200字: {decision_text_stripped[:200]!r}"
        )

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

        # ── 6. 新字段提取 ──────────────────────────────────────────────────
        invalidations = self._extract_invalidations(decision_text)
        entry_zone = self._extract_price_range(decision_text, self._RE_ENTRY_ZONE, "entry_zone")
        target_price = self._extract_price_range(
            decision_text, self._RE_TARGET_PRICE, "target_price"
        )
        stop_loss = self._extract_price_range(decision_text, self._RE_STOP_LOSS, "stop_loss")
        holding_period = self._extract_holding_period(decision_text)
        catalysts = self._extract_catalysts(decision_text)
        scores = self._extract_scores(decision_text)

        return InvestmentDecision(
            signal=signal,
            confidence=round(confidence, 1),
            expected_return=expected_return,
            position_size=position_size,
            horizon=None,
            thesis=thesis,
            risks=risks,
            invalidations=invalidations,
            entry_zone=entry_zone,
            target_price=(target_price[0] if target_price else None),
            stop_loss=(stop_loss[0] if stop_loss else None),
            expected_holding_period=holding_period,
            **scores,
            catalysts=catalysts,
        )

    # ── JSON 结构化解析 ─────────────────────────────────────────────────────

    @staticmethod
    def _try_parse_json(text: str) -> Optional[InvestmentDecision]:
        """
        尝试将输入解析为 JSON 结构化决策。

        支持的字段（全部可选）：
          signal, confidence, thesis, risks, expected_return, position_size,
          invalidations, entry_zone, target_price, stop_loss,
          expected_holding_period, catalysts,
          valuation_score, fundamental_score, technical_score,
          sentiment_score, capital_flow_score, macro_score
        """
        from loguru import logger

        try:
            data = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return None

        if not isinstance(data, dict):
            logger.warning(
                f"[TA决策解析] JSON 解析成功但非对象类型（{type(data).__name__}），"
                f"回退到文本正则解析。"
            )
            return None

        # ── signal ──────────────────────────────────────────────────────────
        signal_raw = data.get("signal", "HOLD")
        if isinstance(signal_raw, str):
            try:
                signal = Signal(signal_raw)
            except ValueError:
                try:
                    signal = Signal(signal_raw.upper())
                except ValueError:
                    logger.warning(f"[TA决策解析] 未知 signal 值 '{signal_raw}'，回退到 HOLD")
                    signal = Signal.HOLD
        else:
            signal = Signal.HOLD

        # ── confidence ──────────────────────────────────────────────────────
        confidence = data.get("confidence")
        if confidence is not None:
            try:
                confidence = float(confidence)
                confidence = max(0.0, min(100.0, confidence))
            except (ValueError, TypeError):
                confidence = 50.0
        else:
            confidence = {Signal.BUY: 80.0, Signal.HOLD: 50.0, Signal.SELL: 30.0}[signal]

        # ── thesis ──────────────────────────────────────────────────────────
        thesis = data.get("thesis", "")
        if isinstance(thesis, str):
            thesis = thesis.strip()[:THESIS_TRUNCATE_LEN]
        else:
            thesis = ""

        # ── risks ───────────────────────────────────────────────────────────
        risks_raw = data.get("risks")
        if isinstance(risks_raw, list):
            risks = [str(r).strip() for r in risks_raw if str(r).strip()]
        elif isinstance(risks_raw, str):
            risks = [r.strip() for r in re.split(r"[,\n]+", risks_raw) if r.strip()]
        else:
            risks = []
        risks = risks[:8]

        # ── expected_return ─────────────────────────────────────────────────
        expected_return = data.get("expected_return")
        if expected_return is not None:
            try:
                expected_return = float(expected_return)
            except (ValueError, TypeError):
                expected_return = None

        # ── position_size ───────────────────────────────────────────────────
        position_size = data.get("position_size")
        if position_size is not None:
            try:
                position_size = float(position_size)
                position_size = max(-1.0, min(1.0, position_size))
            except (ValueError, TypeError):
                position_size = None

        # ── invalidations ───────────────────────────────────────────────────
        invalidations_raw = data.get("invalidations")
        if isinstance(invalidations_raw, list):
            invalidations = [str(r).strip() for r in invalidations_raw if str(r).strip()]
        elif isinstance(invalidations_raw, str):
            invalidations = [r.strip() for r in re.split(r"[,\n]+", invalidations_raw) if r.strip()]
        else:
            invalidations = []
        invalidations = invalidations[:8]

        # ── entry_zone ──────────────────────────────────────────────────────
        entry_zone = data.get("entry_zone")
        if isinstance(entry_zone, list) and len(entry_zone) == 2:
            try:
                entry_zone = [float(entry_zone[0]), float(entry_zone[1])]
            except (ValueError, TypeError):
                entry_zone = None

        # ── target_price / stop_loss ────────────────────────────────────────
        target_price = data.get("target_price")
        if target_price is not None:
            try:
                target_price = float(target_price)
            except (ValueError, TypeError):
                target_price = None

        stop_loss = data.get("stop_loss")
        if stop_loss is not None:
            try:
                stop_loss = float(stop_loss)
            except (ValueError, TypeError):
                stop_loss = None

        # ── expected_holding_period ─────────────────────────────────────────
        holding_period = data.get("expected_holding_period")
        if holding_period is not None:
            try:
                holding_period = int(holding_period)
            except (ValueError, TypeError):
                holding_period = None

        # ── catalysts ───────────────────────────────────────────────────────
        catalysts_raw = data.get("catalysts")
        if isinstance(catalysts_raw, list):
            catalysts = [str(r).strip() for r in catalysts_raw if str(r).strip()]
        elif isinstance(catalysts_raw, str):
            catalysts = [r.strip() for r in re.split(r"[,\n]+", catalysts_raw) if r.strip()]
        else:
            catalysts = []

        # ── 多因子评分 ──────────────────────────────────────────────────────
        def _parse_score(key: str, default: Optional[float] = None) -> Optional[float]:
            v = data.get(key)
            if v is not None:
                try:
                    f = float(v)
                    return max(0.0, min(100.0, f))
                except (ValueError, TypeError):
                    return default
            return default

        kwargs = {
            "valuation_score": _parse_score("valuation_score"),
            "fundamental_score": _parse_score("fundamental_score"),
            "technical_score": _parse_score("technical_score"),
            "sentiment_score": _parse_score("sentiment_score"),
            "capital_flow_score": _parse_score("capital_flow_score"),
            "macro_score": _parse_score("macro_score"),
        }

        logger.info(
            f"[TA决策解析] JSON 结构化解析成功 | signal={signal.value} confidence={confidence}"
        )
        return InvestmentDecision(
            signal=signal,
            confidence=round(confidence, 1),
            expected_return=expected_return,
            position_size=position_size,
            horizon=None,
            thesis=thesis,
            risks=risks,
            invalidations=invalidations,
            entry_zone=entry_zone,
            target_price=target_price,
            stop_loss=stop_loss,
            expected_holding_period=holding_period,
            catalysts=catalysts,
            **kwargs,
        )

    # ── 文本路径提取方法 ──────────────────────────────────────────────────

    def _extract_thesis(self, text: str) -> str:
        m = self._RE_THESIS.search(text)
        if m:
            return m.group(1).strip()[:THESIS_TRUNCATE_LEN]
        m = self._RE_SUMMARY.search(text)
        if m:
            return m.group(1).strip()[:THESIS_TRUNCATE_LEN]
        first_sentence = re.split(r"[。！？\n]", text.strip())
        return first_sentence[0][:200] if first_sentence else ""

    def _extract_risks(self, text: str) -> list[str]:
        risks: list[str] = []
        risk_marker_pos = -1
        for kw in [
            "风险",
            "风险点",
            "担忧",
            "压力",
            "隐患",
            "不利因素",
            "risks",
            "Risks",
            " Risks ",
            " risk ",
        ]:
            pos = text.find(kw)
            if pos >= 0 and (risk_marker_pos < 0 or pos < risk_marker_pos):
                risk_marker_pos = pos
        if risk_marker_pos >= 0:
            chunk = text[risk_marker_pos : risk_marker_pos + 800]
            for line in chunk.split("\n"):
                line = line.strip()
                if not line:
                    continue
                stripped = re.sub(r"^[\s]*[-•*]?\s*\d+[.）)]?\s*", "", line)
                stripped = re.sub(r"^[\s]*[-•*]\s*", "", stripped)
                stripped = stripped.strip()
                if 10 <= len(stripped) <= 120:
                    if stripped not in risks and len(risks) < 8:
                        risks.append(stripped)
        return risks

    def _extract_expected_return(self, text: str, signal: Signal) -> Optional[float]:
        _FIN_RATIO_RE = re.compile(r"\b(pe|peg|pb|eps|roe|roa)\b", re.IGNORECASE)
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
                        return pct
                    if signal == Signal.HOLD and abs(pct) <= 5:
                        return round(pct, 2)
        return None

    def _extract_position_size(self, text: str) -> Optional[float]:
        m = self._RE_POS_SIZE.search(text)
        if m:
            try:
                return float(m.group(1)) / 100.0
            except (ValueError, ZeroDivisionError):
                pass
        return None

    def _extract_invalidations(self, text: str) -> list[str]:
        """提取失效条件列表。"""
        result: list[str] = []

        # 尝试结构化标记
        m = self._RE_INVALIDATIONS.search(text)
        if m:
            block = m.group(1).strip()
            for line in block.split("\n"):
                line = line.strip()
                if not line:
                    continue
                stripped = re.sub(r"^[\s]*[-•*]?\s*\d+[.）)]?\s*", "", line)
                stripped = re.sub(r"^[\s]*[-•*]\s*", "", stripped)
                if 5 <= len(stripped) <= 120:
                    if stripped not in result and len(result) < 8:
                        result.append(stripped)

        # 也检查常见的中文表述
        if not result:
            for pattern in [
                r"(?:如果|若|一旦).+?(?:就|便|则)?.+?(?:卖出|止损|放弃)",
            ]:
                for m2 in re.finditer(pattern, text):
                    stmt = m2.group(0).strip()
                    if 10 <= len(stmt) <= 150 and stmt not in result:
                        result.append(stmt)
                        if len(result) >= 5:
                            break

        return result[:8]

    def _extract_price_range(
        self, text: str, pattern: re.Pattern, key: str
    ) -> Optional[list[float]]:
        """
        从文本中提取价格区间或单一价格。

        Returns
        -------
        [low, high] 或 [single, single] 或 None
        """
        m = pattern.search(text)
        if not m:
            return None
        raw = m.group(1).strip()
        # 区间格式: "148-152" / "148至152" / "148~152"
        separators = r"[-–—~到至]"
        parts = re.split(separators, raw)
        if len(parts) == 2:
            try:
                return [float(parts[0].strip()), float(parts[1].strip())]
            except (ValueError, TypeError):
                pass
        elif len(parts) == 1:
            # 单一价格
            try:
                val = float(parts[0].strip())
                return [val, val]
            except (ValueError, TypeError):
                pass
        return None

    def _extract_holding_period(self, text: str) -> Optional[int]:
        m = self._RE_HOLDING_PERIOD.search(text)
        if m:
            try:
                return int(m.group(1))
            except (ValueError, TypeError):
                pass
        return None

    def _extract_catalysts(self, text: str) -> list[str]:
        result: list[str] = []
        m = self._RE_CATALYSTS.search(text)
        if m:
            block = m.group(1).strip()
            for line in block.split("\n"):
                line = line.strip()
                if not line:
                    continue
                stripped = re.sub(r"^[\s]*[-•*]?\s*\d+[.）)]?\s*", "", line)
                stripped = re.sub(r"^[\s]*[-•*]\s*", "", stripped)
                if 5 <= len(stripped) <= 150:
                    if stripped not in result and len(result) < 8:
                        result.append(stripped)
        return result

    def _extract_scores(self, text: str) -> dict[str, Optional[float]]:
        """提取多因子评分。"""
        score_map: dict[str, Optional[float]] = {
            "valuation_score": None,
            "fundamental_score": None,
            "technical_score": None,
            "sentiment_score": None,
            "capital_flow_score": None,
            "macro_score": None,
        }
        _PATTERNS = {
            "valuation_score": r"估值[:：]?\s*(\d+(?:\.\d+)?)",
            "fundamental_score": r"基本面[:：]?\s*(\d+(?:\.\d+)?)",
            "technical_score": r"技术面[:：]?\s*(\d+(?:\.\d+)?)",
            "sentiment_score": r"情绪[:：]?\s*(\d+(?:\.\d+)?)",
            "capital_flow_score": r"资金流向[:：]?\s*(\d+(?:\.\d+)?)",
            "macro_score": r"宏观[:：]?\s*(\d+(?:\.\d+)?)",
        }
        for key, pat in _PATTERNS.items():
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                try:
                    score_map[key] = float(m.group(1))
                except (ValueError, TypeError):
                    pass
        return score_map

    # ── 兜底解析 ──────────────────────────────────────────────────────────

    def _fallback_signal_from_rating(self, rating_str: str) -> tuple[Signal, float]:
        s = rating_str.lower()
        if "overweight" in s:
            return Signal.OVERWEIGHT, 70.0
        if any(k in s for k in ("buy", "strong")):
            return Signal.BUY, 70.0
        if any(k in s for k in ("sell", "underweight")):
            return Signal.SELL, 35.0
        return Signal.HOLD, 50.0

    @staticmethod
    def _has_negative_before(words: list[str], target: str, window: int = 10) -> bool:
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
