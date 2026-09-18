"""DecisionAdapter — 从 LLM 输出解析结构化投资决定。

从 trade_krono_cli.ta_decision 模块提取，避免循环依赖。
模块级常量 (_RATING_MAP / _NEG_WORDS / THESIS_TRUNCATE_LEN) 已在 __init__.py 中定义，
此文件通过同包导入获取。
"""

from __future__ import annotations

import contextlib
import re

from loguru import logger

from trade_krono_cli.ta_decision import (
    _NEG_WORDS,
    _RATING_MAP,
    THESIS_TRUNCATE_LEN,
    InvestmentDecision,
    Signal,
)
from trade_krono_cli.ta_decision.patterns import (
    RE_CATALYSTS,
    RE_ENTRY_ZONE,
    RE_HOLDING_PERIOD,
    RE_INVALIDATIONS,
    RE_PCT,
    RE_POS_SIZE,
    RE_RATING,
    RE_STOP_LOSS,
    RE_SUMMARY,
    RE_TARGET_PRICE,
    RE_THESIS,
)


class DecisionAdapter:
    """将 TradingAgents 输出解析为结构化 InvestmentDecision。

    解析优先级：
      1. JSON 结构化输出 → 直接映射字段
      2. **Rating**: <value> → 信号 + 基础置信度
      3. **Investment Thesis** / **Executive Summary** → thesis
      4. 百分比数字模式 → expected_return
      5. keyword fallback（负上下文感知）→ 兜底信号
      6. fallback → HOLD, 50
    """

    # Rating 正则
    _RE_RATING = RE_RATING
    # Thesis 段落
    _RE_THESIS = RE_THESIS
    # Executive Summary
    _RE_SUMMARY = RE_SUMMARY
    # 百分比数字
    _RE_PCT = RE_PCT
    # 持仓比例
    _RE_POS_SIZE = RE_POS_SIZE
    # 止损相关
    _RE_STOP_LOSS = RE_STOP_LOSS
    _RE_TARGET_PRICE = RE_TARGET_PRICE
    _RE_ENTRY_ZONE = RE_ENTRY_ZONE

    # 持有期（更宽松的匹配）
    _RE_HOLDING_PERIOD = RE_HOLDING_PERIOD
    # 失效条件
    _RE_INVALIDATIONS = RE_INVALIDATIONS
    # 催化剂
    _RE_CATALYSTS = RE_CATALYSTS

    def parse(self, decision_text: str) -> InvestmentDecision:
        """主入口：解析 LLM 输出 → InvestmentDecision。

        优先尝试 JSON 结构化解析，失败后回退到自由文本正则解析。
        """
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
            f"  原始输出前200字: {decision_text_stripped[:200]!r}",
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
            decision_text,
            self._RE_TARGET_PRICE,
            "target_price",
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
            horizon=holding_period,
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
    def _try_parse_json(text: str) -> "InvestmentDecision | None":
        """尝试将输入解析为 JSON 结构化决策（委托给 parsers 模块）。"""
        from trade_krono_cli.ta_decision.parsers import parse_json  # noqa: PLC0415
        return parse_json(text)

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

    def _extract_expected_return(self, text: str, signal: Signal) -> float | None:
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

    def _extract_position_size(self, text: str) -> float | None:
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
        self,
        text: str,
        pattern: re.Pattern,
        key: str,
    ) -> list[float] | None:
        """从文本中提取价格区间或单一价格。

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

    def _extract_holding_period(self, text: str) -> int | None:
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

    def _extract_scores(self, text: str) -> dict[str, float | None]:
        """提取多因子评分。"""
        score_map: dict[str, float | None] = {
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
                with contextlib.suppress(ValueError, TypeError):
                    score_map[key] = float(m.group(1))
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
