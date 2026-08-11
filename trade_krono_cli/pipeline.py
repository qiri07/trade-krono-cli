"""
流水线编排：TA 选股 + Kronos 预测（并行） → 综合打分 → 输出报告。
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional, Callable

from loguru import logger
from trade_krono_cli.config import get_settings
from trade_krono_cli.ta_runner import TradingAgentsRunner, StockAnalysisResult
from trade_krono_cli.kronos_runner import KronosRunner, KronosForecastResult
from trade_krono_cli.merge import merge_results, filter_pool, default_scorer
from trade_krono_cli.report import save_json, save_html, print_table, print_summary
from trade_krono_cli.cache import get_research


class QuantPipeline:
    """
    一站式投研流水线：
      1. TradingAgents 批量分析（线程1）
      2. Kronos 批量预测   （线程2）
      3. 两者并行完成后融合打分
    """

    def __init__(
        self,
        ta_runner: Optional[TradingAgentsRunner] = None,
        kronos_runner: Optional[KronosRunner] = None,
        scorer: Optional[Callable] = None,
        min_confidence: Optional[float] = None,
        allowed_signals: Optional[tuple[str, ...]] = None,
        skip_kronos: bool = False,
    ):
        self._settings = get_settings()
        self.ta = ta_runner or TradingAgentsRunner()
        self.kronos = None if skip_kronos else (kronos_runner or KronosRunner())
        self.scorer = scorer or default_scorer
        self.min_confidence = min_confidence or self._settings.default_min_confidence
        signals = allowed_signals or tuple(
            s.upper() for s in self._settings.default_allowed_signals
        )
        self.allowed_signals = signals

        logger.info(
            f"🏭 QuantPipeline 就绪 | "
            f"min_confidence={self.min_confidence} "
            f"allowed_signals={self.allowed_signals} "
            f"skip_kronos={skip_kronos}"
        )

    def run_parallel(
        self,
        tickers: list[str],
        date: str,
        output_json: Optional[str] = None,
        output_html: Optional[str] = None,
        progress_cb: Optional[Callable[[str, int, int], None]] = None,
    ) -> list[dict]:
        """
        并行运行 TA + Kronos，合并结果。

        Parameters
        ----------
        tickers : 股票代码列表
        date : 分析日期 YYYY-MM-DD
        output_json : JSON 输出路径（可选）
        output_html : HTML 输出路径（可选）
        progress_cb : fn(stage, current, total)

        Returns
        -------
        排序后的综合结果列表
        """
        t0 = time.time()
        logger.info(
            f"🚀 流水线启动（并行）| {len(tickers)} 只候选 | date={date}"
        )

        # ── 创建研究作业记录（含版本快照）─────────────────
        research = get_research()
        job_id = research.create_job(date, tickers, settings=self._settings)

        if progress_cb:
            progress_cb("启动", 0, 2)

        # ── 并行执行 TA + Kronos ───────────────────────────
        with ThreadPoolExecutor(max_workers=2) as executor:
            ta_future = executor.submit(
                self.ta.analyze_batch, tickers, date
            )
            kronos_future = executor.submit(
                self.kronos.predict_batch, tickers, date
            ) if self.kronos else None

            # 等待两者完成
            ta_results = ta_future.result()
            if kronos_future:
                kronos_results = kronos_future.result()
            else:
                kronos_results = []

        # ── 合并 + 打分 ────────────────────────────────────
        merged = merge_results(ta_results, kronos_results, scorer=self.scorer)

        # ── 落盘 ───────────────────────────────────────────
        # 保存结构化结果 JSON（含摘要报告，用于展示和后续分析）
        if output_json:
            save_json(merged, output_json)
        if output_html:
            save_html(merged, output_html, date)

        # 保存完整原始报告（永不截断，用于 RAG / 回测 / 历史研究）
        raw_paths = self.ta.save_raw_reports(ta_results, date)

        # ── 写入研究数据库（含版本快照）────────────────────
        # 从 jobs 表获取本次运行的版本信息
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
                    job_id, r.ticker, r.investment_decision,
                    r.investment_decision.thesis, r.investment_decision.risks,
                )

        self._index_ta_raw_reports(research, job_id, ta_results, raw_paths)

        for r in kronos_results:
            research.insert_kronos(job_id, r, version_snapshot=version_snapshot)

        research.insert_signals(job_id, merged, version_snapshot=version_snapshot)

        elapsed = time.time() - t0
        research.complete_job(job_id, n_success=len(merged), elapsed=elapsed)
        logger.info(
            f"📊 研究作业完成: job={job_id} run_id={version_snapshot['run_id']} "
            f"| 耗时 {elapsed:.1f}s | 结果 {len(merged)} 条 → 已记录到研究数据库"
        )

        if progress_cb:
            progress_cb("完成", 2, 2)

        return merged

    def run_ta_only(
        self,
        tickers: list[str],
        date: str,
        output: Optional[str] = None,
        progress_cb: Optional[Callable[[int, int, StockAnalysisResult], None]] = None,
    ) -> list[StockAnalysisResult]:
        """仅运行 TA 分析。"""
        t0 = time.time()
        logger.info(f"🚀 TA 分析启动 | {len(tickers)} 只 | date={date}")

        research = get_research()
        job_id = research.create_job(date, tickers, settings=self._settings)

        results = self.ta.analyze_batch(tickers, date, progress_cb=progress_cb)
        if output:
            self.ta.save_results(results, output)
        # 同时保存完整原始报告
        raw_paths = self.ta.save_raw_reports(results, date)

        # 获取版本快照
        job_info = research.get_job(job_id)
        version_snapshot = {
            "run_id": job_info["run_id"] if job_info else None,
            "data_version": job_info["data_version"] if job_info else None,
            "model_versions": job_info["model_versions"] if job_info else {},
        }

        # 写入研究数据库
        for r in results:
            research.insert_ta(job_id, r, version_snapshot=version_snapshot)
            if r.investment_decision:
                research.insert_decision(
                    job_id, r.ticker, r.investment_decision,
                    r.investment_decision.thesis, r.investment_decision.risks,
                )

        self._index_ta_raw_reports(research, job_id, results, raw_paths)

        elapsed = time.time() - t0
        research.complete_job(job_id, n_success=sum(1 for r in results if r.error is None), elapsed=elapsed)
        logger.info(f"📊 TA 研究作业完成: job={job_id} run_id={version_snapshot['run_id']}")

        return results

    @staticmethod
    def _index_ta_raw_reports(
        research, job_id: str, ta_results: list, raw_paths: dict,
    ) -> None:
        """索引 TA 原始报告文件到 research database。"""
        from pathlib import Path as _P
        import json as _json
        for r in ta_results:
            if not r.investment_decision:
                continue
            report_path = raw_paths.get(r.ticker)
            if not report_path:
                continue
            raw_file = _P(report_path)
            if not raw_file.exists():
                continue
            try:
                file_data = _json.loads(raw_file.read_text(encoding="utf-8"))
                lengths = {k: len(v) for k, v in file_data.get("reports_raw", {}).items()}
                research.index_raw_report(job_id, r.ticker, str(report_path), lengths)
            except Exception as e:
                logger.warning(f"⚠️  索引原始报告失败 {r.ticker}: {e}")

    def run_kronos_only(
        self,
        tickers: list[str],
        date: str,
        output: Optional[str] = None,
    ) -> list[KronosForecastResult]:
        """仅运行 Kronos 预测。"""
        if self.kronos is None:
            raise RuntimeError("KronosRunner 未初始化（skip_kronos=True）")
        logger.info(f"🚀 Kronos 预测启动 | {len(tickers)} 只 | date={date}")
        results = self.kronos.predict_batch(tickers, date)
        if output:
            self.kronos.save_results(results, output)
        return results
