"""committee/types — 委员会数据类型与报告提取工具。

包含：
  - AgentType / AgentReport / StockCommitteeInput / InvestmentCommitteeResult
  - extract_agent_reports / _extract_key_finding
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from enum import Enum

from loguru import logger

# ═══════════════════════════════════════════════════════
#  数据类型
# ═══════════════════════════════════════════════════════


class AgentType(str, Enum):
    """委员会中各 Agent 的角色类型。"""

    FUNDAMENTAL = "fundamental"
    MARKET = "market"
    SENTIMENT = "sentiment"
    NEWS = "news"
    POLICY = "policy"
    CAPITAL_FLOW = "capital_flow"  # HotMoney
    LOCKUP = "lockup"
    KRONOS = "kronos"
    TECHNICAL = "technical"


@dataclass(frozen=True)
class AgentReport:
    """单个 Agent 的分析报告。

    Attributes
    ----------
    agent_type      : 报告来源（AgentType 枚举）
    ticker          : 股票代码
    content         : 原始报告文本（摘要）
    signal          : Agent 独立判断的信号（BUY/HOLD/SELL/None）
    confidence      : Agent 独立置信度（0-100，None 表示未明确表达）
    key_finding     : 最关键的发现（一句话）

    """

    agent_type: AgentType
    ticker: str
    content: str
    signal: str | None = None
    confidence: float | None = None
    key_finding: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["agent_type"] = self.agent_type.value
        return d


@dataclass
class StockCommitteeInput:
    """单只股票的委员会审议输入。

    Attributes
    ----------
    ticker             : 股票代码
    date               : 评估日期
    agent_reports      : 各 Agent 的结构化报告列表
    kronos_direction   : Kronos 预测方向（UP/DOWN/FLAT）
    kronos_change_pct  : Kronos 预期涨跌幅（%）
    kronos_confidence  : Kronos 置信度（0-100）
    ta_signal          : TA 综合信号
    ta_confidence      : TA 综合置信度
    composite_score    : 合并打分

    """

    ticker: str
    date: str
    agent_reports: list[AgentReport] = field(default_factory=list)
    kronos_direction: str | None = None
    kronos_change_pct: float | None = None
    kronos_confidence: float | None = None
    ta_signal: str | None = None
    ta_confidence: float | None = None
    composite_score: float | None = None

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "date": self.date,
            "agent_reports": [r.to_dict() for r in self.agent_reports],
            "kronos_direction": self.kronos_direction,
            "kronos_change_pct": self.kronos_change_pct,
            "kronos_confidence": self.kronos_confidence,
            "ta_signal": self.ta_signal,
            "ta_confidence": self.ta_confidence,
            "composite_score": self.composite_score,
        }


@dataclass
class InvestmentCommitteeResult:
    """委员会审议结果。

    Attributes
    ----------
    ticker              : 股票代码
    date                : 评估日期
    job_id              : 关联研究作业 ID
    run_id              : 关联运行 ID
    bull_case           : 看多论点摘要
    bear_case           : 看空论点摘要
    recommendation      : 委员会推荐（BUY/HOLD/SELL）
    recommendation_confidence : 委员会推荐置信度（0-100）
    reasoning           : 完整审议推理链
    agent_consensus     : Agent 信号分布（如 {"BUY": 3, "HOLD": 2, "SELL": 1}）
    created_at          : 创建时间戳

    """

    ticker: str
    date: str
    job_id: str
    run_id: str
    bull_case: str = ""
    bear_case: str = ""
    recommendation: str = "HOLD"
    recommendation_confidence: float = 50.0
    reasoning: str = ""
    agent_consensus: dict[str, int] = field(default_factory=dict)
    created_at: float = 0.0

    def __post_init__(self):
        if self.created_at == 0.0:
            self.created_at = time.time()

    def to_dict(self) -> dict:
        return asdict(self)


# ═══════════════════════════════════════════════════════
#  报告提取工具
# ═══════════════════════════════════════════════════════

_AGENT_TYPE_MAP: dict[str, AgentType] = {
    "fundamentals_report": AgentType.FUNDAMENTAL,
    "market_report": AgentType.MARKET,
    "sentiment_report": AgentType.SENTIMENT,
    "news_report": AgentType.NEWS,
    "policy_report": AgentType.POLICY,
    "hot_money_report": AgentType.CAPITAL_FLOW,
    "lockup_report": AgentType.LOCKUP,
}


def extract_agent_reports(
    final_state: dict,
    ticker: str,
) -> list[AgentReport]:
    """从 TradingAgents final_state 提取结构化的 Agent 报告。

    Parameters
    ----------
    final_state : TradingAgents graph.invoke() 返回的完整状态 dict
    ticker      : 股票代码

    Returns
    -------
    list[AgentReport]

    """
    reports: list[AgentReport] = []
    for key, agent_type in _AGENT_TYPE_MAP.items():
        content = final_state.get(key, "")
        if not content:
            continue
        key_finding = _extract_key_finding(content, agent_type)
        reports.append(
            AgentReport(
                agent_type=agent_type,
                ticker=ticker,
                content=content[:2000],
                key_finding=key_finding,
            ),
        )

    # 提取辩论历史作为补充证据
    debate_state = final_state.get("investment_debate_state", {})
    if isinstance(debate_state, dict):
        bull_history = debate_state.get("bull_history", "")
        bear_history = debate_state.get("bear_history", "")
        if bull_history:
            reports.append(
                AgentReport(
                    agent_type=AgentType.FUNDAMENTAL,
                    ticker=ticker,
                    content=bull_history[:1000],
                    key_finding="Bull debate highlights",
                ),
            )
        if bear_history:
            reports.append(
                AgentReport(
                    agent_type=AgentType.FUNDAMENTAL,
                    ticker=ticker,
                    content=bear_history[:1000],
                    key_finding="Bear debate highlights",
                ),
            )

    logger.info(f"📡 委员会输入: {ticker} | {len(reports)} 份 Agent 报告已提取")
    return reports


def _extract_key_finding(content: str, agent_type: AgentType) -> str:
    """从报告文本中提取关键发现（首句或前100字）。"""
    if not content:
        return ""
    first_sentence = content.split("\n", maxsplit=1)[0].strip()
    first_sentence = first_sentence.lstrip("#* ").strip()
    return first_sentence[:150] if first_sentence else content[:150]


__all__ = (
    "AgentReport",
    "AgentType",
    "InvestmentCommitteeResult",
    "StockCommitteeInput",
    "extract_agent_reports",
)
