"""InvestmentCommittee — AI 投资委员会核心审议逻辑。

从 tradingagents 的 final_state 中提取结构化报告，
通过启发式或 LLM 路径生成 Bull/Bear Case 和最终推荐。
"""

from __future__ import annotations

import json
import re
from typing import Any

from loguru import logger

# 向后兼容：直接重新导出（保持 trade_krono_cli.committee.AgentType 可用）
from trade_krono_cli.committee.types import (
    AgentReport,
    AgentType,
    InvestmentCommitteeResult,
    StockCommitteeInput,
    extract_agent_reports,
)

# ═══════════════════════════════════════════════════════
#  InvestmentCommittee — 核心审议逻辑
# ═══════════════════════════════════════════════════════


class InvestmentCommittee:
    """AI 投资委员会。

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
        llm_provider: str | None = None,
        llm_api_key: str | None = None,
        llm_base_url: str | None = None,
    ) -> None:
        self._llm_provider = llm_provider
        self._llm_api_key = llm_api_key
        self._llm_base_url = llm_base_url

    def deliberate(
        self,
        input_data: StockCommitteeInput,
        llm_client: Any | None = None,
    ) -> InvestmentCommitteeResult:
        """执行委员会审议。

        Parameters
        ----------
        input_data  : 单只股票的委员会输入
        llm_client  : LLM 客户端（可选，None 时使用启发式路径）

        Returns
        -------
        InvestmentCommitteeResult

        """
        ticker = input_data.ticker
        consensus = self._compute_consensus(input_data)

        if llm_client is not None:
            bull_case, bear_case, recommendation, confidence, reasoning = self._llm_deliberate(
                input_data,
                llm_client,
                consensus,
            )
        else:
            bull_case, bear_case, recommendation, confidence, reasoning = (
                self._heuristic_deliberate(input_data, consensus)
            )

        return InvestmentCommitteeResult(
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

    def get_consensus(self, input_data: StockCommitteeInput) -> dict[str, int]:
        """快捷方法：仅计算 Agent 信号共识分布。"""
        return self._compute_consensus(input_data)

    # ── 内部方法 ──────────────────────────────────────────────────────────

    @staticmethod
    def _compute_consensus(
        input_data: StockCommitteeInput,
    ) -> dict[str, int]:
        """统计各 Agent 的信号分布。

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
        """启发式审议（无需 LLM 调用）。
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
                f"(conf={input_data.kronos_confidence:.0f})",
            )
        elif input_data.kronos_direction == "DOWN":
            bear_points.append(
                f"【Kronos】量化模型预测下跌 {abs(float(input_data.kronos_change_pct or 0)):.1f}% "
                f"(conf={input_data.kronos_confidence:.0f})",
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
        llm_client: Any,  # noqa: ANN401 — 支持多种 LLM 客户端实现（OpenAI / Anthropic / 自定义）
        consensus: dict[str, int],
    ) -> tuple[str, str, str, float, str]:
        """LLM 增强审议路径。

        将结构化报告 + 共识分布发送给 LLM，生成更精细的 Bull/Bear Case 和最终推荐。
        构建 prompt → 调用 LLM → 解析 JSON 输出。失败时降级到启发式路径。
        """
        try:
            prompt = self._build_llm_prompt(input_data, consensus)
            response_text = self._call_llm(llm_client, prompt)
            result = self._parse_llm_response(response_text, input_data, consensus)
            return (
                result["bull_case"],
                result["bear_case"],
                result["recommendation"],
                result["confidence"],
                result["reasoning"],
            )
        except Exception as e:
            logger.warning(f"⚠️  LLM 审议失败，降级到启发式路径: {e}")
            return self._heuristic_deliberate(input_data, consensus)

    @staticmethod
    def _build_llm_prompt(input_data: StockCommitteeInput, consensus: dict[str, int]) -> str:
        """构建委员会审议的 LLM prompt。"""
        agents = "\n".join(
            f"- {r.agent_type.value}: {r.signal or 'N/A'} | {r.key_finding}"
            for r in input_data.agent_reports
        )
        return (
            "你是一个专业的投资顾问。请基于以下信息进行分析，输出 JSON。\n\n"
            f"股票: {input_data.ticker}\n"
            f"日期: {input_data.date}\n"
            f"Agent 共识: {json.dumps(consensus, ensure_ascii=False)}\n"
            f"TA 信号: {input_data.ta_signal} (置信度={input_data.ta_confidence})\n"
            f"Kronos: {input_data.kronos_direction} ({input_data.kronos_change_pct}%, 置信度={input_data.kronos_confidence})\n"
            f"综合评分: {input_data.composite_score}\n\n"
            "Agent 报告:\n"
            f"{agents}\n\n"
            "请以 JSON 格式输出，包含以下字段：\n"
            '{"bull_case": "看多论点", "bear_case": "看空论点", '
            '"recommendation": "BUY/HOLD/SELL/OVERWEIGHT", '
            '"confidence": 75.0, "reasoning": "完整推理"}'
        )

    @staticmethod
    def _call_llm(llm_client: Any, prompt: str) -> str:  # noqa: ANN401 — LLM 客户端接口多态，支持多种实现
        """调用 LLM 并返回文本响应。支持多种客户端接口。"""
        if hasattr(llm_client, "generate"):
            return llm_client.generate(prompt)
        if hasattr(llm_client, "chat"):
            return llm_client.chat(prompt)
        msg = "llm_client 不支持 generate() 或 chat() 方法"
        raise RuntimeError(msg)

    @staticmethod
    def _parse_llm_response(
        response_text: str,
        input_data: StockCommitteeInput,
        consensus: dict[str, int],
    ) -> dict[str, Any]:
        """解析 LLM 返回的 JSON 响应。"""
        try:
            result = json.loads(response_text)
        except json.JSONDecodeError:
            # 尝试从文本中提取 JSON 块
            match = re.search(r"\{[^{}]+\}", response_text, re.DOTALL)
            if match:
                result = json.loads(match.group(0))
            else:
                msg = f"无法解析 LLM 响应: {response_text[:200]}"
                raise ValueError(msg)

        for key in ("bull_case", "bear_case", "recommendation", "confidence", "reasoning"):
            if key not in result:
                msg = f"LLM 响应缺少字段: {key}"
                raise ValueError(msg)
        return result


# ═══════════════════════════════════════════════════════
#  模块级便捷函数
# ═══════════════════════════════════════════════════════


def build_committee_input(
    ticker: str,
    date: str,
    final_state: dict,
    kronos_result: dict | None = None,
    ta_signal: str | None = None,
    ta_confidence: float | None = None,
    composite_score: float | None = None,
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


__all__ = (
    "AgentReport",
    "AgentType",
    "InvestmentCommittee",
    "InvestmentCommitteeResult",
    "StockCommitteeInput",
    "build_committee_input",
    "describe_committee",
)
