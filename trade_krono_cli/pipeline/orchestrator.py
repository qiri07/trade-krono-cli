"""
orchestrator — 调度主循环。

QuantPipeline 的核心实现，负责协调 TA + Kronos 并行执行、
结果合并、落盘和数据库写入。

PipelineFactory 负责组装组件（TradingAgentsRunner / KronosRunner），
实现创建与执行的职责分离。
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional, Callable

from loguru import logger
from trade_krono_cli.config import get_settings, Settings
from trade_krono_cli.ta_runner import TradingAgentsRunner, StockAnalysisResult
from trade_krono_cli.kronos_runner import KronosRunner, KronosForecastResult
from trade_krono_cli.pipeline.merge import merge_results, filter_pool
from trade_krono_cli.research_db import get_research
from trade_krono_cli.trading_constraints import T1Tracker
from trade_krono_cli.constraints_config import ConstraintConfig
from trade_krono_cli.pipeline_config import PipelineConfig
from trade_krono_cli.pipeline.data_fetcher import prepare_kline_batch
from trade_krono_cli.pipeline.reporter import (
    save_json_report,
    save_html_report,
)
from trade_krono_cli.security import sanitize_for_log


def _collect_futures(
    ta_future, kronos_future,
) -> tuple[list, list]:
    """
    同时等待两个 Future 完成，Kronos 异常时降级为空列表。

    关键：不先后调用 .result()，而是统一等待，确保不会浪费"并行"时间。
    """
    try:
        ta_results = ta_future.result()
    except Exception as e:
        safe_msg = sanitize_for_log(str(e))
        logger.error(f"⚠️  TA 批量分析线程异常: {safe_msg}")
        ta_results = []

    if kronos_future is None:
        return ta_results, []

    try:
        kronos_results = kronos_future.result()
    except Exception as e:
        safe_msg = sanitize_for_log(str(e))
        logger.error(f"⚠️  Kronos 批量预测线程异常: {safe_msg}")
        kronos_results = []

    return ta_results, kronos_results


class PipelineFactory:
    """
    流水线组件工厂。

    负责根据 Settings / PipelineConfig 组装 TradingAgentsRunner 和
    KronosRunner，将「组件创建」与「执行调度」解耦。

    用法：
        # 生产路径：完全由工厂创建
        ta, kronos = PipelineFactory.create(settings, config, no_cache=False)

        # 测试注入：传入 mock runner，工厂补全缺失的另一个
        ta, kronos = PipelineFactory.create(
            settings, config, no_cache=True,
            ta_runner=mock_ta,   # 仅注入 TA，Kronos 由工厂创建
        )
    """

    @staticmethod
    def create(
        settings: Settings,
        config: PipelineConfig,
        no_cache: bool = False,
        constraints_config: Optional[ConstraintConfig] = None,
        sample_count: Optional[int] = None,
        skip_kronos: bool = False,
        ta_runner: Optional[TradingAgentsRunner] = None,
        kronos_runner: Optional[KronosRunner] = None,
    ) -> tuple[TradingAgentsRunner, Optional[KronosRunner]]:
        """
        创建流水线组件。

        Parameters
        ----------
        settings          : 全局配置
        config            : 流水线配置
        no_cache          : 是否禁用缓存
        constraints_config : 交易约束配置（None 时使用 config 默认值）
        sample_count      : Kronos 采样次数（None 时使用 config 默认值）
        skip_kronos       : 是否跳过 Kronos
        ta_runner         : 测试注入的 TA runner（None 时由工厂创建）
        kronos_runner     : 测试注入的 Kronos runner（None 时由工厂创建）

        Returns
        -------
        (ta_runner, kronos_runner)
          kronos_runner 在 skip_kronos=True 或无注入时为 None
        """
        if ta_runner is None:
            ta_runner = TradingAgentsRunner(no_cache=no_cache, settings=settings)
        if skip_kronos:
            return ta_runner, None
        if kronos_runner is None:
            kronos_runner = KronosRunner(
                no_cache=no_cache,
                sample_count=sample_count,
                settings=settings,
            )
        return ta_runner, kronos_runner


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
        config: Optional[PipelineConfig] = None,
        # 向后兼容参数
        min_confidence: Optional[float] = None,
        allowed_signals: Optional[tuple[str, ...]] = None,
        skip_kronos: bool = False,
        no_cache: bool = False,
        constraints_config: Optional[ConstraintConfig] = None,
        sample_count: Optional[int] = None,
        settings: Optional[Settings] = None,
    ):
        self._settings = settings or get_settings()
        self._config = config or PipelineConfig.default()

        # 参数优先级：显式参数 > config > settings
        self.constraints_config = constraints_config or self._config.constraints
        self._sample_count = sample_count
        self.min_confidence = min_confidence or self._config.min_confidence
        signals = allowed_signals or self._config.allowed_signals
        self.allowed_signals = signals
        self.no_cache = no_cache

        # 组件创建：显式传入 > 工厂创建
        self.ta, self.kronos = PipelineFactory.create(
            settings=self._settings,
            config=self._config,
            no_cache=no_cache,
            constraints_config=constraints_config,
            sample_count=sample_count,
            skip_kronos=skip_kronos,
            ta_runner=ta_runner,
            kronos_runner=kronos_runner,
        )

        logger.info(
            f"🏭 QuantPipeline 就绪 | "
            f"min_confidence={self.min_confidence} "
            f"allowed_signals={self.allowed_signals} "
            f"skip_kronos={skip_kronos} "
            f"constraints={'enabled' if self.constraints_config.enable_limit_check else 'disabled'} "
            f"sample_count={'(default)' if sample_count is None else sample_count}"
        )

    def run_parallel(
        self,
        tickers: list[str],
        date: str,
        output_json: Optional[str] = None,
        output_html: Optional[str] = None,
        progress_cb: Optional[Callable[[str, int, int], None]] = None,
    ) -> list[dict]:
        """并行运行 TA + Kronos，合并结果。"""
        t0 = time.time()
        logger.info(
            f"🚀 流水线启动（并行）| {len(tickers)} 只候选 | date={date}"
        )

        research = get_research()
        job_id = research.create_job(date, tickers, settings=self._settings)

        if progress_cb:
            progress_cb("启动", 0, 2)

        # ── 批量准备 K 线数据（共享给风险引擎）─────────────────
        kline_data = prepare_kline_batch(
            tickers, date,
            lookback=self._config.lookback,
            adjustflag=self.constraints_config.adjustflag,
            use_cache=self._config.use_cache,
        )

        # ── 并行执行 TA + Kronos（错误隔离）──────────────────
        with ThreadPoolExecutor(max_workers=2) as executor:
            ta_future = executor.submit(
                self.ta.analyze_batch, tickers, date
            )
            kronos_future = executor.submit(
                self.kronos.predict_batch, tickers, date
            ) if self.kronos else None

            ta_results, kronos_results = _collect_futures(ta_future, kronos_future)

        # ── 应用过滤（信号 / 置信度阈值）────────────────────
        filtered_ta = filter_pool(
            ta_results,
            min_confidence=self.min_confidence,
            allowed_signals=self.allowed_signals,
        )

        # ── 合并 + 打分（含交易约束）────────────────────────────
        t1_tracker = T1Tracker()
        merged = merge_results(
            filtered_ta,
            kronos_results,
            kline_data=kline_data,
            constraints_config=self.constraints_config,
            t1_tracker=t1_tracker,
        )

        # ── 落盘 ───────────────────────────────────────────
        if output_json:
            save_json_report(merged, output_json)
        if output_html:
            save_html_report(merged, output_html, date)

        raw_paths = self.ta.save_raw_reports(ta_results, date)

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
                    job_id, r.ticker, r.investment_decision,
                    r.investment_decision.thesis, r.investment_decision.risks,
                )

        self._index_ta_raw_reports(research, job_id, ta_results, raw_paths)

        for kr in kronos_results:
            research.insert_kronos(job_id, kr, version_snapshot=version_snapshot)

        research.insert_signals(job_id, merged, version_snapshot=version_snapshot)

        elapsed = time.time() - t0
        n_success = sum(1 for r in ta_results if r.error is None)
        research.complete_job(job_id, n_success=n_success, elapsed=elapsed)
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
        raw_paths = self.ta.save_raw_reports(results, date)

        job_info = research.get_job(job_id)
        version_snapshot = {
            "run_id": job_info["run_id"] if job_info else None,
            "data_version": job_info["data_version"] if job_info else None,
            "model_versions": job_info["model_versions"] if job_info else {},
        }

        for r in results:
            research.insert_ta(job_id, r, version_snapshot=version_snapshot)
            if r.investment_decision:
                research.insert_decision(
                    job_id, r.ticker, r.investment_decision,
                    r.investment_decision.thesis, r.investment_decision.risks,
                )

        self._index_ta_raw_reports(research, job_id, results, raw_paths)

        elapsed = time.time() - t0
        research.complete_job(
            job_id,
            n_success=sum(1 for r in results if r.error is None),
            elapsed=elapsed,
        )
        logger.info(
            f"📊 TA 研究作业完成: job={job_id} run_id={version_snapshot['run_id']}"
        )
        return results

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

    @staticmethod
    def _index_ta_raw_reports(
        research, job_id: str, ta_results: list, raw_paths: dict,
    ) -> None:
        """索引 TA 原始报告文件到 research database。"""
        for r in ta_results:
            if not r.investment_decision:
                continue
            report_path = raw_paths.get(r.ticker)
            if not report_path:
                continue
            raw_file = Path(report_path)
            if not raw_file.exists():
                continue
            try:
                file_data = json.loads(raw_file.read_text(encoding="utf-8"))
                lengths = {k: len(v) for k, v in file_data.get("reports_raw", {}).items()}
                research.index_raw_report(job_id, r.ticker, str(report_path), lengths)
            except (OSError, ValueError) as e:
                # 已知文件/格式错误
                logger.warning(f"⚠️  索引原始报告失败 {r.ticker}: {e}")
            except Exception as e:
                # 未预料错误：脱敏记录
                safe_msg = sanitize_for_log(str(e))
                logger.warning(f"⚠️  索引原始报告异常 {r.ticker}: {safe_msg}")
