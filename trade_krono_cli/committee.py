"""
InvestmentCommittee — AI 投资委员会。

TradingAgents 已经内置了一个完整的分析团队：
  Fundamental / Market / Sentiment / News / Policy / HotMoney / Lockup
  Bull Researcher / Bear Researcher / Risk Debators / Trader / Portfolio Manager

当前系统只取用了最终文本输出（final_trade_decision）。
本模块将其升级为结构化的「委员会审议」流程：

  ┌─────────────────────────────────────────────────────┐
  │              Investment Committee                   │
  │                                                     │
  │  Fundamental Report  ─┐                            │
  │  Market Report       ─┤                            │
  │  Sentiment Report    ─┤   ┌── Bull Case            │
  │  News Report         ─┤──▶│                          │
  │  Policy Report       ─┤   │   ┌─ Final Decision     │
  │  HotMoney Report     ─┤   └── Bear Case  ──────────▶│
  │  Lockup Report       ─┤                            │
  │  Kronos Prediction   ─┘                            │
  │  Technical Scores    ─┘                            │
  └─────────────────────────────────────────────────────┘

设计原则：
  · 每个 Agent 的报告是独立的证据源，委员会必须综合全部输入
  · Bull Case / Bear Case 必须显式呈现，便于审计
  · 委员会决策是可解释的（不是黑盒 LLM 调用）
  · 所有审议记录持久化到研究数据库
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Optional

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
    """
    单个 Agent 的分析报告。

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
    signal: Optional[str] = None
    confidence: Optional[float] = None
    key_finding: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["agent_type"] = self.agent_type.value
        return d


@dataclass
class StockCommitteeInput:
    """
    单只股票的委员会审议输入。

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
    kronos_direction: Optional[str] = None
    kronos_change_pct: Optional[float] = None
    kronos_confidence: Optional[float] = None
    ta_signal: Optional[str] = None
    ta_confidence: Optional[float] = None
    composite_score: Optional[float] = None

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
    """
    委员会审议结果。

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
    """
    从 TradingAgents final_state 提取结构化的 Agent 报告。

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
        # 尝试从内容中提取信号和关键发现（简化版，实际可接入 LLM）
        key_finding = _extract_key_finding(content, agent_type)
        reports.append(
            AgentReport(
                agent_type=agent_type,
                ticker=ticker,
                content=content[:2000],  # 截断以防过大
                key_finding=key_finding,
            )
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
                )
            )
        if bear_history:
            reports.append(
                AgentReport(
                    agent_type=AgentType.FUNDAMENTAL,
                    ticker=ticker,
                    content=bear_history[:1000],
                    key_finding="Bear debate highlights",
                )
            )

    logger.info(f"📡 委员会输入: {ticker} | {len(reports)} 份 Agent 报告已提取")
    return reports


def _extract_key_finding(content: str, agent_type: AgentType) -> str:
    """从报告文本中提取关键发现（首句或前100字）。"""
    if not content:
        return ""
    # 取第一句作为关键发现
    first_sentence = content.split("\n")[0].strip()
    # 去掉 Markdown 标题标记
    first_sentence = first_sentence.lstrip("#* ").strip()
    return first_sentence[:150] if first_sentence else content[:150]


# ═══════════════════════════════════════════════════════
#  InvestmentCommittee — 核心审议逻辑
# ═══════════════════════════════════════════════════════


class InvestmentCommittee:
    """
    AI 投资委员会。

    接收多 Agent 报告 + Kronos 预测，综合审议后输出：
      - Bull Case / Bear Case 结构化论点
      - 委员会推荐信号及置信度
      - 完整审议推理链（审计用）
      - Agent 信号共识分布

    用法：
        committee = InvestmentCommittee()
        result = committee.deliberate(input_data, llm_client=None)
    """

    def __init__(
        self,
        llm_provider: Optional[str] = None,
        model: Optional[str] = None,
    ) -> None:
        self._llm_provider = llm_provider
        self._model = model
        self._llm_client = None  # 懒加载

    # ── 核心 API ──────────────────────────────────────────────────────────

    def deliberate(
        self,
        input_data: StockCommitteeInput,
        llm_client: Optional[Any] = None,
    ) -> InvestmentCommitteeResult:
        """
        执行委员会审议。

        Parameters
        ----------
        input_data   : StockCommitteeInput 完整输入
        llm_client   : LLM 客户端（可选，None 时使用启发式合成）

        Returns
        -------
        InvestmentCommitteeResult
        """
        ticker = input_data.ticker
        logger.info(f"🏛️  委员会审议启动: {ticker} @ {input_data.date}")

        # 1. 汇总 Agent 信号共识
        consensus = self._compute_consensus(input_data)

        # 2. 综合审议（LLM 路径 or 启发式路径）
        if llm_client is not None:
            bull_case, bear_case, recommendation, confidence, reasoning = self._llm_deliberate(
                input_data, llm_client, consensus
            )
        else:
            bull_case, bear_case, recommendation, confidence, reasoning = (
                self._heuristic_deliberate(input_data, consensus)
            )

        result = InvestmentCommitteeResult(
            ticker=ticker,
            date=input_data.date,
            job_id="",
            run_id="",
            bull_case=bull_case,
            bear_case=bear_case,
            recommendation=recommendation,
            recommendation_confidence=confidence,
            reasoning=reasoning,
            agent_consensus=consensus,
        )

        logger.info(
            f"🏛️  委员会审议完成: {ticker} "
            f"→ {recommendation}(conf={confidence:.0f}) "
            f"| 共识: {consensus}"
        )
        return result

    def get_consensus(self, input_data: StockCommitteeInput) -> dict[str, int]:
        """快捷方法：仅计算 Agent 信号共识分布。"""
        return self._compute_consensus(input_data)

    # ── 内部方法 ──────────────────────────────────────────────────────────

    @staticmethod
    def _compute_consensus(
        input_data: StockCommitteeInput,
    ) -> dict[str, int]:
        """
        统计各 Agent 的信号分布。

        Returns
        -------
        dict : {"BUY": n, "HOLD": m, "SELL": k}
        """
        consensus: dict[str, int] = {"BUY": 0, "OVERWEIGHT": 0, "HOLD": 0, "SELL": 0}

        for report in input_data.agent_reports:
            sig = report.signal
            if sig == "BUY":
                consensus["BUY"] += 1
            elif sig == "OVERWEIGHT":
                consensus["OVERWEIGHT"] += 1
            elif sig == "SELL":
                consensus["SELL"] += 1
            elif sig == "HOLD":
                consensus["HOLD"] += 1

        # TA 综合信号加入共识
        if input_data.ta_signal:
            sig = input_data.ta_signal.upper()
            if sig in consensus:
                consensus[sig] += 1

        # Kronos 预测作为方向性参考（不直接计入信号，但影响权重）
        if input_data.kronos_direction == "UP":
            consensus["_kronos_up"] = 1
        elif input_data.kronos_direction == "DOWN":
            consensus["_kronos_down"] = 1

        return consensus

    def _heuristic_deliberate(
        self,
        input_data: StockCommitteeInput,
        consensus: dict[str, int],
    ) -> tuple[str, str, str, float, str]:
        """
        启发式审议（无需 LLM 调用）。
        基于 Agent 报告内容和共识分布综合判断。
        """
        ticker = input_data.ticker

        # ── 构建 Bull Case ────────────────────────────────────────────────
        bull_points: list[str] = []
        bear_points: list[str] = []

        for report in input_data.agent_reports:
            if report.signal in ("BUY", "OVERWEIGHT"):
                bull_points.append(f"【{report.agent_type.value}】{report.key_finding}")
            elif report.signal == "SELL":
                bear_points.append(f"【{report.agent_type.value}】{report.key_finding}")
            elif report.signal == "HOLD":
                # 中性报告同时写入两侧作为风险/机会
                bull_points.append(f"【{report.agent_type.value}】{report.key_finding}")
                bear_points.append(f"【{report.agent_type.value}】谨慎：{report.key_finding}")

        # Kronos 预测方向加入论点
        if input_data.kronos_direction == "UP":
            bull_points.append(
                f"【Kronos】量化模型预测上涨 {input_data.kronos_change_pct:.1f}% "
                f"(conf={input_data.kronos_confidence:.0f})"
            )
        elif input_data.kronos_direction == "DOWN":
            bear_points.append(
                f"【Kronos】量化模型预测下跌 {abs(input_data.kronos_change_pct):.1f}% "
                f"(conf={input_data.kronos_confidence:.0f})"
            )

        bull_case = "\n".join(bull_points) if bull_points else "无明显看多论点"
        bear_case = "\n".join(bear_points) if bear_points else "无明显看空论点"

        # ── 综合信号判定 ──────────────────────────────────────────────────
        buy_votes = consensus.get("BUY", 0)
        overweight_votes = consensus.get("OVERWEIGHT", 0)
        sell_votes = consensus.get("SELL", 0)
        hold_votes = consensus.get("HOLD", 0)
        total = buy_votes + overweight_votes + sell_votes + hold_votes

        if total == 0:
            recommendation = "HOLD"
            confidence = 50.0
        elif buy_votes + overweight_votes > sell_votes:
            if buy_votes > sell_votes:
                recommendation = "BUY"
                cs = input_data.composite_score or 50
                confidence = min(95.0, 50.0 + buy_votes * 10.0 + cs * 0.2)
            else:
                recommendation = "OVERWEIGHT"
                cs = input_data.composite_score or 50
                confidence = min(95.0, 50.0 + overweight_votes * 8.0 + cs * 0.15)
        elif sell_votes > buy_votes + overweight_votes:
            recommendation = "SELL"
            confidence = min(95.0, 50.0 + sell_votes * 10.0)
        else:
            recommendation = "HOLD"
            confidence = 55.0

        # TA 综合信号加权
        if input_data.ta_signal == "BUY" and input_data.ta_confidence:
            if recommendation == "HOLD":
                recommendation = "BUY"
            confidence = min(95.0, confidence + input_data.ta_confidence * 0.15)
        elif input_data.ta_signal == "OVERWEIGHT" and input_data.ta_confidence:
            if recommendation == "HOLD":
                recommendation = "OVERWEIGHT"
            confidence = min(95.0, confidence + input_data.ta_confidence * 0.10)
        elif input_data.ta_signal == "SELL" and input_data.ta_confidence:
            if recommendation == "HOLD":
                recommendation = "SELL"
            confidence = min(95.0, confidence + input_data.ta_confidence * 0.15)

        confidence = round(min(95.0, max(30.0, confidence)), 1)

        kr_dir = input_data.kronos_direction or ""
        kr_pct = input_data.kronos_change_pct or 0
        kr_conf = input_data.kronos_confidence or 0
        reasoning = (
            f"委员会审议 [{ticker} @ {input_data.date}]\n"
            f"Agent 共识: BUY={buy_votes}, HOLD={hold_votes}, SELL={sell_votes}\n"
            f"TA 信号: {input_data.ta_signal}(conf={input_data.ta_confidence})\n"
            f"Kronos: {kr_dir}({kr_pct}%, conf={kr_conf})\n"
            f"综合评分: {input_data.composite_score}\n\n"
            f"看多论点:\n{bull_case}\n\n"
            f"看空论点:\n{bear_case}"
        )

        return bull_case, bear_case, recommendation, confidence, reasoning

    def _llm_deliberate(
        self,
        input_data: StockCommitteeInput,
        llm_client: Any,
        consensus: dict[str, int],
    ) -> tuple[str, str, str, float, str]:
        """
        LLM 增强审议路径（预留接口）。

        将结构化报告 + 共识分布发送给 LLM，生成更精细的 Bull/Bear Case 和最终推荐。

        TODO: 实现 LLM 审议路径。参考 DecisionAdapter 模式：
              构建 prompt → 调用 LLM → 解析 JSON 输出。
        """
        raise NotImplementedError("LLM 委员会审议路径尚未实现，请使用 heuristic 模式")


# ═══════════════════════════════════════════════════════
#  模块级便捷函数
# ═══════════════════════════════════════════════════════


def build_committee_input(
    ticker: str,
    date: str,
    final_state: dict,
    kronos_result: Optional[dict] = None,
    ta_signal: Optional[str] = None,
    ta_confidence: Optional[float] = None,
    composite_score: Optional[float] = None,
) -> StockCommitteeInput:
    """工厂函数：从 TradingAgents final_state + 外部数据构建委员会输入。"""
    agent_reports = extract_agent_reports(final_state, ticker)

    kronos_dir = None
    kronos_change = None
    kronos_conf = None
    if kronos_result:
        kronos_dir = kronos_result.get("direction")
        kronos_change = kronos_result.get("expected_change_pct")
        pu = kronos_result.get("prediction_uncertainty")
        if isinstance(pu, dict):
            kronos_conf = pu.get("confidence_score")

    return StockCommitteeInput(
        ticker=ticker,
        date=date,
        agent_reports=agent_reports,
        kronos_direction=kronos_dir,
        kronos_change_pct=kronos_change,
        kronos_confidence=kronos_conf,
        ta_signal=ta_signal,
        ta_confidence=ta_confidence,
        composite_score=composite_score,
    )


def describe_committee(result: InvestmentCommitteeResult) -> str:
    """返回委员会审议结果的可读描述。"""
    lines = [
        f"🏛️ {result.ticker} 投资委员会审议",
        f"  日期     : {result.date}",
        f"  推荐     : {result.recommendation}  (置信度={result.recommendation_confidence:.0f})",
        f"  Agent共识: {json.dumps(result.agent_consensus, ensure_ascii=False)}",
        "",
        "  📈 看多论点:",
    ]
    for line in result.bull_case.split("\n"):
        lines.append(f"    {line}")
    lines.append("")
    lines.append("  📉 看空论点:")
    for line in result.bear_case.split("\n"):
        lines.append(f"    {line}")
    lines.append("")
    lines.append("  🔍 审议推理:")
    for line in result.reasoning.split("\n")[:8]:
        lines.append(f"    {line}")
    return "\n".join(lines)
