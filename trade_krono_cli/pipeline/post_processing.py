"""pipeline.post_processing — 流水线后处理模块。

职责（从 QuantPipeline.run_parallel 提取）：
  · 元数据过滤（PE/PB/市值/行业/风险分/异常标记）
  · TA + Kronos 结果融合打分
  · 异常风险分上调
  · 报告落盘（JSON / HTML）
  · 研究数据库写入（TA / Kronos / Signals / Decisions）
  · 委员会审议
  · 作业完成标记

所有函数接收明确的参数，不依赖全局状态。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from loguru import logger

from trade_krono_cli.abnormal_stock import apply_abnormality_risk_boost
from trade_krono_cli.data import fetch_realtime_quote
from trade_krono_cli.pipeline.merge import filter_pool, merge_results
from trade_krono_cli.pipeline.orchestrator import CommitteeOrchestrator
from trade_krono_cli.pipeline.reporter import save_html_report, save_json_report
from trade_krono_cli.research_db import ResearchDatabase
from trade_krono_cli.stock_filter import StockFilter, StockMeta
from trade_krono_cli.ta_runner import StockAnalysisResult
from trade_krono_cli.trading_constraints import T1Tracker

if TYPE_CHECKING:
    from trade_krono_cli.constraints_config import ConstraintConfig
    from trade_krono_cli.pipeline_config import PipelineConfig


def apply_metadata_filter(
    ta_results: list[StockAnalysisResult],
    abnormal_flags_map: dict[str, Any],
    config: "PipelineConfig",
    min_confidence: float,
    allowed_signals: tuple[str, ...],
) -> tuple[list[StockAnalysisResult], dict[str, dict]]:
    """应用股票元数据过滤（PE/PB/市值/行业/风险分/异常标记）。

    Returns
    -------
    tuple[list[StockAnalysisResult], dict[str, dict]]
        (filtered_ta_final, quote_data)
    """
    # ── 应用过滤（信号 / 置信度阈值）────────────────────────────
    filtered_ta = filter_pool(
        ta_results,
        min_confidence=min_confidence,
        allowed_signals=allowed_signals,
    )

    # ── 股票元数据过滤 ────────────────────────────────────────
    filter_engine = StockFilter.from_config(
        min_confidence=min_confidence,
        allowed_signals=allowed_signals,
        market_cap_range=config.market_cap_range,
        industry_whitelist=config.industry_whitelist,
        industry_blacklist=config.industry_blacklist,
        pe_range=config.pe_range,
        pb_range=config.pb_range,
        max_risk_score=config.max_risk_score,
        min_volume_ratio=config.min_volume_ratio,
        min_turnover_rate=config.min_turnover_rate,
        exclude_st=config.exclude_st,
    )

    # 提前收集实时行情数据，供 StockMeta 填充 PE/PB（仅对有效结果请求，避免浪费 timeout）
    live_ta = [r for r in filtered_ta if r.error is None]
    quote_data: dict[str, dict] = {
        tk: fetch_realtime_quote(tk) for tk in [r.ticker for r in live_ta]
    }

    # 构建 StockMeta 并注入异常标记，用于过滤
    filtered_ta_list: list[StockAnalysisResult] = []
    rejected_ta: list[StockAnalysisResult] = []
    for r in filtered_ta:
        if r.error is not None:
            continue
        af = abnormal_flags_map.get(r.ticker)
        flag_names = af.flag_names() if af else []
        severity = af.severity if af else 0.0
        meta = StockMeta(
            signal=r.signal,
            confidence=r.confidence,
            ticker=r.ticker,
            abnormal_flags=flag_names,
            abnormality_score=severity,
            pe_ttm=quote_data.get(r.ticker, {}).get("pe"),
            pb=quote_data.get(r.ticker, {}).get("pb"),
        )
        # ST / 停牌 / 退市股票直接过滤
        blocker_flags = {"ST", "SUSPENDED", "DELISTED"}
        if blocker_flags & set(meta.abnormal_flags):
            rejected_ta.append(r)
            continue
        filtered_ta_list.append(r)

    # 再用常规 StockFilter 过滤（置信度 / 信号等）
    meta_list = [
        StockMeta(
            signal=r.signal,
            confidence=r.confidence,
            ticker=r.ticker,
            pe_ttm=quote_data.get(r.ticker, {}).get("pe"),
            pb=quote_data.get(r.ticker, {}).get("pb"),
        )
        for r in filtered_ta_list
    ]
    passed_metas, rejected_ta_extra = filter_engine.apply_batch(meta_list)
    rejected_ta.extend(cast(list["StockAnalysisResult"], rejected_ta_extra))
    passed_tickers = {m.ticker for m in passed_metas}
    filtered_ta_final = [r for r in filtered_ta_list if r.ticker in passed_tickers]

    logger.info(
        f"📋 元数据过滤完成: 保留 {len(filtered_ta_final)} 只 "
        f"（原始池 {len(filtered_ta)} + 已过滤 {len(rejected_ta)}）",
    )
    return filtered_ta_final, quote_data


def merge_and_boost(
    filtered_ta: list[StockAnalysisResult],
    kronos_results: list,
    kline_data: dict,
    quote_data: dict[str, dict],
    constraints_config: "ConstraintConfig",
    config: "PipelineConfig",
    abnormal_flags_map: dict[str, Any],
) -> list[dict]:
    """合并 TA + Kronos 结果，应用交易约束，并上调异常风险分。

    Returns
    -------
    list[dict]
        融合后的候选股票列表
    """
    # ── 合并 + 打分（含交易约束）────────────────────────────
    t1_tracker = T1Tracker()
    merged = merge_results(
        filtered_ta,
        kronos_results,
        kline_data=kline_data,
        quote_data=quote_data,
        constraints_config=constraints_config,
        t1_tracker=t1_tracker,
        scoring_config=config.scoring,
        risk_config=config.risk,
        scoring_strategy=config.scoring_strategy,
        degrade_mode=config.degrade_mode,
    )

    # ── 异常风险分上调 ──────────────────────────────────────
    if getattr(config, "abnormality_risk_boost_enabled", True):
        risk_boost_cfg = config.risk_boost_strategy
        for item in merged:
            ticker = item.get("ticker", "")
            af = abnormal_flags_map.get(ticker)
            if af and af.flags:
                base_risk = item.get("risk_score_total") or 50.0
                boosted = apply_abnormality_risk_boost(
                    base_risk_score=base_risk,
                    flags=af.flag_names(),
                    enabled=True,
                    strategy=risk_boost_cfg.strategy,
                    params=risk_boost_cfg.params if hasattr(risk_boost_cfg, "params") else None,
                )
                item["risk_score_total"] = boosted
                item["abnormal_flags"] = af.flag_names()

    return merged


def write_results(
    merged: list[dict],
    ta_results: list[StockAnalysisResult],
    kronos_results: list,
    research: ResearchDatabase,
    job_id: str,
    date: str,
    output_json: str | None,
    output_html: str | None,
    config: Any,
) -> None:
    """将合并结果写入数据库、生成报告。"""
    # ── 落盘 ───────────────────────────────────────────
    if output_json:
        save_json_report(merged, output_json)
    if output_html:
        save_html_report(merged, output_html, date)

    # ── 写入研究数据库 ────────────────────────────────
    job_info = research.get_job(job_id)
    version_snapshot = {
        "run_id": job_info["run_id"] if job_info else None,
        "data_version": job_info["data_version"] if job_info else None,
        "model_versions": job_info["model_versions"] if job_info else {},
    }

    for r in ta_results:
        research.insert_ta(job_id, r, version_snapshot=version_snapshot)
        if r.investment_decision:
            research.insert_decision(
                job_id,
                r.ticker,
                r.investment_decision,
                r.investment_decision.thesis,
                r.investment_decision.risks,
            )

    # ── 写入 Kronos 预测 ────────────────────────────────
    for kr in kronos_results:
        research.insert_kronos(job_id, kr, version_snapshot=version_snapshot)

    research.insert_signals(job_id, merged, version_snapshot=version_snapshot)


def run_committee(
    research: ResearchDatabase,
    job_id: str,
    date: str,
    ta_results: list[StockAnalysisResult],
    kronos_results: list,
) -> None:
    """对每只 TA 分析过的股票运行 Investment Committee 审议。"""
    orchestrator = CommitteeOrchestrator(research)
    orchestrator.run(job_id, date, ta_results, kronos_results)
