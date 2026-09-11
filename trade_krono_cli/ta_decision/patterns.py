"""Regex patterns for DecisionAdapter text parsing.

Extracted from adapter_impl.py to enable independent testing of pattern
matching logic without instantiating the full adapter.
"""

from __future__ import annotations

import re

#: Rating 标签（**Rating**: BUY / **Rating**：强买入）
RE_RATING = re.compile(
    r"\*\*Rating\*\*\s*[:：]\s*([A-Za-z]+(?:\s+[A-Za-z]+)?)",
    re.IGNORECASE,
)
#: Investment Thesis 段落
RE_THESIS = re.compile(
    r"\*\*Investment Thesis\*\*\s*[:：]\s*(.+?)(?=\n\*\*|\Z)",
    re.DOTALL | re.IGNORECASE,
)
#: Executive Summary 段落
RE_SUMMARY = re.compile(
    r"\*\*Executive Summary\*\*\s*[:：]\s*(.+?)(?=\n\*\*|\Z)",
    re.DOTALL | re.IGNORECASE,
)
#: 百分比数字（5%、12.5%）
RE_PCT = re.compile(r"(?<![\d./])(\d+(?:\.\d+)?)\s*%")
#: 持仓比例（仓位: 30% 以上）
RE_POS_SIZE = re.compile(r"仓位[:：]?\s*(\d+(?:\.\d+)?)\s*[%‰]?\s*(?:以上|左右|)")
#: 止损价（止损: 140-145 / 止损≥140）
RE_STOP_LOSS = re.compile(
    r"(?:止损|stop\s*loss)[:：]?\s*([\d.,]+\s*[-–—至到]\s*[\d.,]+|[≥≤><=]?\s*[\d.,]+)",
    re.IGNORECASE,
)
#: 目标价（目标价: 200-220 / target price 200）
RE_TARGET_PRICE = re.compile(
    r"(?:目标价|target\s*(?:price|price\s*target)|目标)[:：]?\s*([\d.,]+\s*[-–—至到]\s*[\d.,]+|[≥≤><=]?\s*[\d.,]+)",
    re.IGNORECASE,
)
#: 入场区间（入场区间: 148-152 / entry zone 148）
RE_ENTRY_ZONE = re.compile(
    r"(?:入场区?间|entry\s*(?:zone|price)|建议买入)[:：]?\s*([\d.,]+\s*[-–—至到]\s*[\d.,]+|[≥≤><=]?\s*[\d.,]+)",
    re.IGNORECASE,
)
#: 持有期（持有期: 30 / holding period 60）
RE_HOLDING_PERIOD = re.compile(
    r"(?:持有期|holding\s*period|预期持有)[:：]?\s*(\d+)",
    re.IGNORECASE,
)
#: 失效条件块
RE_INVALIDATIONS = re.compile(
    r"(?:失效条件|invalidation|if.*?则?卖出|逻辑失效)[:：]?\s*(.+?)(?=\n\s*(?:风险|catalyst|\*\*|#####)|\Z)",
    re.DOTALL | re.IGNORECASE,
)
#: 催化剂段落
RE_CATALYSTS = re.compile(
    r"\*\*Catalysts\*\*\s*[:：]\s*(.+?)(?=\n\*\*|\Z)",
    re.DOTALL | re.IGNORECASE,
)
