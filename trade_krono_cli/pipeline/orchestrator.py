"""Pipeline 流水线编排辅助模块。

包含：
- CommitteeOrchestrator: 委员会审议编排（从 pipeline_core 拆出）
- ReportIndexer: TA 原始报告索引（从 pipeline_core 拆出）
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from trade_krono_cli.committee import InvestmentCommittee, build_committee_input
from trade_krono_cli.kronos_runner import KronosForecastResult
from trade_krono_cli.security import sanitize_for_log

if TYPE_CHECKING:
    pass


class CommitteeOrchestrator:
    """委员会审议编排器。

    负责对每只 TA 分析过的股票运行 Investment Committee 审议，
    并将审议结果写入研究数据库。
    """

    def __init__(self, research: Any) -> None:  # noqa: ANN401
        """初始化委员会编排器。

        Parameters
        ----------
        research : ResearchDatabase
            研究数据库实例（运行时注入，避免循环导入）
        """
        self._research = research

    def run(
        self,
        job_id: str,
        date: str,
        ta_results: list[Any],  # noqa: ANN401 — TAAnalysis 在运行时注入
        kronos_results: list[Any],  # noqa: ANN401 — KronosForecastResult 在运行时注入
    ) -> None:
        """对每只 TA 分析过的股票运行 Investment Committee 审议。

        跳过无 final_state（分析失败）或无 agent reports 的股票。
        审议结果写入 committee_deliberations 表。

        Parameters
        ----------
        job_id : str
            作业 ID
        date : str
            评估日期
        ta_results : list[TAAnalysis]
            TA 分析结果列表
        kronos_results : list[KronosForecastResult]
            Kronos 预测结果列表
        """
        # 构建 kronos lookup: ticker → forecast dict
        kronos_map: dict[str, dict] = self._build_kronos_map(kronos_results)

        committee = InvestmentCommittee()
        deliberated = 0

        for ta in ta_results:
            if ta.error is not None:
                continue
            final_state = getattr(ta, "final_state", None)
            if not final_state:
                continue

            kr = kronos_map.get(ta.ticker)
            try:
                committee_input = build_committee_input(
                    ticker=ta.ticker,
                    date=date,
                    final_state=final_state,
                    kronos_result=kr,
                    ta_signal=ta.signal,
                    ta_confidence=ta.confidence,
                    composite_score=None,  # 由合并打分提供，此处为 TA-only 路径
                )
                result = committee.deliberate(committee_input)
                result.job_id = job_id
                # 获取 run_id（从 research 中取 job 信息）
                job_info = self._research.get_job(job_id)
                result.run_id = job_info.get("run_id", "") if job_info else ""
                self._research.insert_committee_deliberation(
                    job_id=job_id,
                    ticker=ta.ticker,
                    date=date,
                    bull_case=result.bull_case,
                    bear_case=result.bear_case,
                    recommendation=result.recommendation,
                    recommendation_confidence=result.recommendation_confidence,
                    reasoning=result.reasoning,
                    agent_consensus=result.agent_consensus,
                )
                deliberated += 1
                logger.info(
                    f"🏛️  委员会审议完成: {ta.ticker} "
                    f"→ {result.recommendation}(conf={result.recommendation_confidence:.0f})",
                )
            except Exception as e:
                safe_msg = sanitize_for_log(str(e))
                logger.warning(f"⚠️  委员会审议失败 {ta.ticker}: {safe_msg}")

        if deliberated:
            logger.info(f"🏛️  委员会审议: {deliberated}/{len(ta_results)} 只股票完成")

    @staticmethod
    def _build_kronos_map(
        kronos_results: list[Any],
    ) -> dict[str, dict]:
        """构建 Kronos 结果查找表。

        Parameters
        ----------
        kronos_results : list[KronosForecastResult | dict]
            Kronos 预测结果列表

        Returns
        -------
        dict[str, dict]
            ticker → forecast dict 映射
        """
        kronos_map: dict[str, dict] = {}
        for kr in kronos_results:
            if isinstance(kr, KronosForecastResult):
                pu = kr.prediction_uncertainty
                kronos_map[kr.ticker] = {
                    "direction": kr.direction,
                    "expected_change_pct": kr.expected_change_pct,
                    "prediction_uncertainty": pu.to_dict() if pu else {},
                }
            elif isinstance(kr, dict):
                kronos_map[kr.get("ticker", "")] = kr
        return kronos_map


class ReportIndexer:
    """TA 原始报告索引器。

    负责将 TA 原始报告文件索引到研究数据库。
    """

    def __init__(self, research: Any) -> None:  # noqa: ANN401
        """初始化报告索引器。

        Parameters
        ----------
        research : ResearchDatabase
            研究数据库实例
        """
        self._research = research

    def index(
        self,
        job_id: str,
        ta_results: list[Any],  # noqa: ANN401
        raw_paths: dict[str, str],
    ) -> None:
        """索引 TA 原始报告文件到研究数据库。

        Parameters
        ----------
        job_id : str
            作业 ID
        ta_results : list[TAAnalysis]
            TA 分析结果列表
        raw_paths : dict[str, str]
            ticker → 报告路径 映射
        """
        for r in ta_results:
            if not getattr(r, "investment_decision", None):
                continue
            report_path = raw_paths.get(getattr(r, "ticker", ""))
            if not report_path:
                continue
            raw_file = Path(report_path)
            if not raw_file.exists():
                continue
            try:
                file_data = json.loads(raw_file.read_text(encoding="utf-8"))
                lengths = {k: len(v) for k, v in file_data.get("reports_raw", {}).items()}
                self._research.index_raw_report(job_id, r.ticker, str(report_path), lengths)
            except (OSError, ValueError) as e:
                # 已知文件/格式错误
                logger.warning(f"⚠️  索引原始报告失败 {r.ticker}: {e}")
            except Exception as e:
                # 未预料错误：脱敏记录
                safe_msg = sanitize_for_log(str(e))
                logger.warning(f"⚠️  索引原始报告异常 {r.ticker}: {safe_msg}")


__all__ = ("CommitteeOrchestrator", "ReportIndexer")
