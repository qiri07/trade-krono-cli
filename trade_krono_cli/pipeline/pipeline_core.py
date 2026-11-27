"""pipeline/pipeline_core — QuantPipeline 核心实现。

负责 TA + Kronos 并行执行、结果合并、落盘和数据库写入。
从原 orchestrator.py 拆出，保持向后兼容的导入路径。

后处理逻辑已拆分至：
  - pipeline.post_processing.run_post_processing
  - pipeline.cache_fallback.apply_ta_cache_fallback
  - pipeline.unified_decisions.attach_unified_decisions
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from collections.abc import Callable

from trade_krono_cli.abnormal_stock import (
    AbnormalityFlag,
    StockAbnormality,
    check_kline_completeness,
    precheck_stock_status,
)
from trade_krono_cli.config import Settings, get_settings
from trade_krono_cli.pipeline.cache_fallback import apply_ta_cache_fallback
from trade_krono_cli.pipeline.data_fetcher import prepare_kline_batch
from trade_krono_cli.pipeline.factory import PipelineFactory
from trade_krono_cli.pipeline.orchestrator import CommitteeOrchestrator, ReportIndexer
from trade_krono_cli.pipeline.post_processing import run_post_processing
from trade_krono_cli.pipeline_config import PipelineConfig
from trade_krono_cli.research_db import get_research

if TYPE_CHECKING:
    from trade_krono_cli.backtest_position_manager import DynamicPositionManager
    from trade_krono_cli.backtest_risk_control import MaxDrawdownControl
    from trade_krono_cli.constraints_config import ConstraintConfig
    from trade_krono_cli.kronos_runner import KronosForecastResult
    from trade_krono_cli.ta_runner import StockAnalysisResult
    from trade_krono_cli.universe.engine import UniverseEngine


class QuantPipeline:
    """一站式投研流水线：
    1. TradingAgents 批量分析（线程1）
    2. Kronos 批量预测   （线程2）
    3. 两者并行完成后融合打分.

    支持配置化并发：
      · max_workers: 最大并发线程数（默认 2）
      · task_timeout: 单任务超时（秒）
      · deadline: 整体截止时间（秒）
    """

    def __init__(
        self,
        ta_session: Any | None = None,
        kronos_session: Any | None = None,
        config: PipelineConfig | None = None,
        # 向后兼容参数（创建默认 session 时使用）
        min_confidence: float | None = None,
        allowed_signals: tuple[str, ...] | None = None,
        skip_kronos: bool = False,
        no_cache: bool = False,
        constraints_config: ConstraintConfig | None = None,
        sample_count: int | None = None,
        settings: Settings | None = None,
        # 向后兼容：直接注入 runner
        ta_runner: Any | None = None,
        kronos_runner: Any | None = None,
        # Universe Engine（可选：无 tickers 时自动从全市场生成）
        universe_engine: UniverseEngine | None = None,
        # 并发配置
        max_workers: int = 2,
        task_timeout: float | None = None,
        deadline: float | None = None,
        # 风险控制（可选：注入 MaxDrawdownControl 用于实盘风控）
        risk_control: MaxDrawdownControl | None = None,
        # 动态仓位管理（可选：基于得分的仓位分配）
        position_manager: DynamicPositionManager | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._config = config or PipelineConfig.default()

        # 参数优先级：显式参数 > config > settings
        self.constraints_config = constraints_config or self._config.constraints
        self._sample_count = sample_count
        self.min_confidence = min_confidence or self._config.min_confidence
        signals = allowed_signals or self._config.allowed_signals
        self.allowed_signals = signals
        self.no_cache = no_cache
        self._universe_engine = universe_engine
        if risk_control is not None:
            self.risk_control = risk_control
        else:
            from trade_krono_cli.backtest_risk_control import (  # noqa: PLC0415
                MaxDrawdownControl,
            )
            self.risk_control = MaxDrawdownControl()
        if position_manager is not None:
            self.position_manager = position_manager
        else:
            from trade_krono_cli.backtest_position_manager import (  # noqa: PLC0415
                DynamicPositionManager,
            )
            self.position_manager = DynamicPositionManager()

        # 兼容旧的 ta_runner / kronos_runner 参数
        if ta_runner is not None and ta_session is None:
            ta_session = ta_runner
        if kronos_runner is not None and kronos_session is None:
            kronos_session = kronos_runner

        # 组件创建：显式传入 > 工厂创建
        self.ta_session, self.kronos_session = PipelineFactory.create(
            settings=self._settings,
            config=self._config,
            no_cache=no_cache,
            constraints_config=constraints_config,
            sample_count=sample_count,
            skip_kronos=skip_kronos,
            ta_session=ta_session,
            kronos_session=kronos_session,
        )

        # 并发配置
        self.max_workers = max_workers
        self.task_timeout = task_timeout
        self.deadline = deadline

        logger.info(
            f"🏭 QuantPipeline 就绪 | "
            f"max_workers={max_workers} "
            f"task_timeout={task_timeout} "
            f"deadline={deadline} "
            f"min_confidence={self.min_confidence} "
            f"allowed_signals={self.allowed_signals} "
            f"skip_kronos={skip_kronos} "
            f"constraints={'enabled' if self.constraints_config.enable_limit_check else 'disabled'} "
            f"sample_count={'(default)' if sample_count is None else sample_count}",
        )

        # 构造 runner 实例，委托 session 管理资源
        from trade_krono_cli.retry_policy import RetryPolicy

        retry_cfg = config or PipelineConfig.default()
        retry_policy = RetryPolicy(
            max_attempts=retry_cfg.retry_max_attempts,
            base_delay=retry_cfg.retry_base_delay,
            jitter=retry_cfg.retry_jitter,
            rate_limit_backoff=retry_cfg.retry_rate_limit_backoff,
            rate_limit_max_wait=retry_cfg.retry_rate_limit_max_wait,
        )
        self.ta = self.ta_session.runner
        self.kronos = self.kronos_session.runner if self.kronos_session else None
        # 注入重试策略
        if hasattr(self.ta, "_retry_policy"):
            self.ta._retry_policy = retry_policy
        if self.kronos and hasattr(self.kronos, "_retry_policy"):
            self.kronos._retry_policy = retry_policy

        logger.info(
            f"🏭 QuantPipeline 就绪 | "
            f"min_confidence={self.min_confidence} "
            f"allowed_signals={self.allowed_signals} "
            f"skip_kronos={skip_kronos} "
            f"constraints={'enabled' if self.constraints_config.enable_limit_check else 'disabled'} "
            f"sample_count={'(default)' if sample_count is None else sample_count}",
        )

    def run_parallel(
        self,
        tickers: list[str],
        date: str,
        output_json: str | None = None,
        output_html: str | None = None,
        progress_cb: Callable[[str, int, int], None] | None = None,
        streaming: bool = False,
    ) -> list[dict]:
        """并行运行 TA + Kronos，合并结果。

        Parameters
        ----------
        streaming : 是否启用流式模式。True 时数据拉取与计算重叠执行，
                    总耗时 ≈ max(T_fetch, T_compute)；默认 False 使用传统批量模式。

        """
        # ── 自动宇宙解析：无 tickers 时从全市场生成 ─────────────────────
        if not tickers and self._universe_engine is not None:
            logger.info("🌌 未指定股票列表，启动 Universe Engine 自动发现...")
            tickers = self._universe_engine.run(eval_date=date)
            if not tickers:
                logger.warning("Universe Engine 返回空列表，流水线终止")
                return []
            logger.info(f"🌌 Universe Engine 产出 {len(tickers)} 只候选股票")
        elif not tickers:
            logger.warning("未提供股票列表且无 Universe Engine，流水线终止")
            return []

        logger.info(
            f"🚀 流水线启动{'（流式）' if streaming else '（并行）'}| "
            f"{len(tickers)} 只候选 | date={date}",
        )

        research = get_research()
        job_id = research.create_job(date, tickers, settings=self._settings)
        self._progress_cb = progress_cb
        self._start_time = time.time()

        if progress_cb:
            progress_cb("启动", 0, 2)

        # ── 异常股票预检（在 TA/Kronos 执行前批量检测）───────────────
        cfg = self._config
        abnormal_flags_map = precheck_stock_status(
            tickers=tickers,
            eval_date=date,
            min_listing_days=getattr(cfg, "new_stock_min_days", 60),
            skip_suspended=cfg.exclude_st or True,
            skip_new_stock=getattr(cfg, "skip_new_stock", True),
        )
        # 记录被标记为退市的股票
        delisted = [
            t for t, f in abnormal_flags_map.items() if StockAbnormality.DELISTED in f.flags
        ]
        if delisted:
            logger.warning(
                f"🚫 检测到 {len(delisted)} 只退市股票，将从分析中排除: {', '.join(delisted)}",
            )
        # 过滤掉退市股票，不参与后续分析
        tickers_clean: list[str] = []
        for t in tickers:
            flags = abnormal_flags_map.get(t)
            if flags and StockAbnormality.DELISTED in flags.flags:
                continue
            tickers_clean.append(t)
        tickers = tickers_clean

        # 初始化 kline_data（流式模式由 StreamPipeline 内部预取，此处保持兼容）
        kline_data: dict = {}

        # ── 流式模式：数据拉取与计算重叠 ────────────────────────────────
        if streaming:
            from trade_krono_cli.pipeline.stream_pipeline import StreamPipeline

            stream = StreamPipeline(
                ta_runner=self.ta,
                kronos_runner=self.kronos,
                no_cache=self.no_cache,
                progress_cb=progress_cb,
                pred_len=self._config.kronos.pred_len,
            )
            ta_results, kronos_results, stream_kline_data = stream.run(
                tickers=tickers,
                date=date,
                lookback=self._config.lookback,
                adjustflag=self.constraints_config.adjustflag,
                use_cache=self._config.use_cache,
            )
            # 流式模式下 kline_data 由 StreamPipeline 内部预取
            kline_data = stream_kline_data
        else:
            # ── 批量准备 K 线数据（共享给风险引擎）─────────────────
            kline_data = prepare_kline_batch(
                tickers,
                date,
                lookback=self._config.lookback,
                adjustflag=self.constraints_config.adjustflag,
                use_cache=self._config.use_cache,
            )

            # ── K 线完整性校验（标记数据不足的股票）─────────────────
            min_completeness = getattr(cfg, "kline_min_completeness", 0.85)
            for tk, df in list(kline_data.items()):
                if df is None or len(df) == 0:
                    abnormal_flags_map[tk] = AbnormalityFlag(
                        ticker=tk,
                        flags=[StockAbnormality.DATA_INSUFFICIENT],
                        severity=0.5,
                        reason="K 线数据为空",
                    )
                    logger.warning(f"⚠️  {tk} K 线数据为空，标记为 DATA_INSUFFICIENT")
                    continue
                passed, reason = check_kline_completeness(df, tk, min_completeness)
                if not passed:
                    existing = abnormal_flags_map.get(tk)
                    if existing and StockAbnormality.DATA_INSUFFICIENT not in existing.flags:
                        # AbnormalityFlag is frozen — create a new instance
                        new_flags = [*existing.flags, StockAbnormality.DATA_INSUFFICIENT]
                        abnormal_flags_map[tk] = AbnormalityFlag(
                            ticker=tk,
                            flags=new_flags,
                            severity=max(existing.severity, 0.5),
                            reason=existing.reason or reason,
                        )
                    logger.warning(f"⚠️  {reason}")

            # ── 并行执行 TA + Kronos（错误隔离）──────────────────
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                ta_future = executor.submit(self.ta.analyze_batch, tickers, date)
                kronos_future = (
                    executor.submit(self.kronos.predict_batch, tickers, date)
                    if self.kronos
                    else None
                )

                # 等待 TA 结果（允许失败）
                try:
                    ta_results = ta_future.result(timeout=self.task_timeout)
                except Exception as e:
                    logger.error(f"❌ TA 批处理失败: {e}")
                    ta_results = []

                # 等待 Kronos 结果（允许失败）
                if kronos_future:
                    try:
                        kronos_results = kronos_future.result(timeout=self.task_timeout)
                    except Exception as e:
                        logger.error(f"❌ Kronos 批处理失败: {e}")
                        kronos_results = []
                else:
                    kronos_results = []

        # ── 降级模式：TA 缓存回退 ────────────────────────────────
        if (
            self._config.degrade_mode == "ta_cache_fallback"
            and self._config.ta_cache_fallback_enabled
        ):
            apply_ta_cache_fallback(
                ta_results=ta_results,
                max_age_days=self._config.ta_cache_max_age_days,
                research=research,
            )

        # ── 后处理：元数据过滤、合并、风控、落盘、委员会、统一决策 ──
        merged = run_post_processing(
            ta_results=ta_results,
            kronos_results=kronos_results,
            kline_data=kline_data,
            abnormal_flags_map=abnormal_flags_map,
            config=self._config,
            constraints_config=self.constraints_config,
            min_confidence=self.min_confidence,
            allowed_signals=self.allowed_signals,
            research=research,
            job_id=job_id,
            date=date,
            output_json=output_json,
            output_html=output_html,
            ta_session=self.ta_session,
            risk_control=self.risk_control,
            position_manager=self.position_manager,
            start_time=self._start_time,
            progress_cb=progress_cb,
        )

        return merged

    def _apply_ta_cache_fallback(self, ta_results: list) -> int:
        """TA 缓存回退（向后兼容，委托 pipeline.cache_fallback.apply_ta_cache_fallback）。

        Parameters
        ----------
        ta_results : list
            TA 分析结果列表（会被原地修改，替换失败项）。

        Returns
        -------
        int
            回退成功的股票数量。
        """
        return apply_ta_cache_fallback(
            ta_results=ta_results,
            max_age_days=self._config.ta_cache_max_age_days,
            research=get_research(),
        )

    def run_ta_only(
        self,
        tickers: list[str],
        date: str,
        output: str | None = None,
        progress_cb: Callable[[int, int, StockAnalysisResult], None] | None = None,
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
                    job_id,
                    r.ticker,
                    r.investment_decision,
                    r.investment_decision.thesis,
                    r.investment_decision.risks,
                )

        self._index_ta_raw_reports(research, job_id, results, raw_paths)

        # ── 委员会审议（TA-only 路径）──────────────────────────────
        self._run_committee(
            research=research,
            job_id=job_id,
            date=date,
            ta_results=results,
            kronos_results=[],
        )

        elapsed = time.time() - t0
        research.complete_job(
            job_id,
            n_success=sum(1 for r in results if r.error is None),
            elapsed=elapsed,
        )
        logger.info(f"📊 TA 研究作业完成: job={job_id} run_id={version_snapshot['run_id']}")
        return results

    def run_kronos_only(
        self,
        tickers: list[str],
        date: str,
        output: str | None = None,
    ) -> list[KronosForecastResult]:
        """仅运行 Kronos 预测。"""
        if self.kronos is None:
            msg = "KronosSession 未初始化（skip_kronos=True）"
            raise RuntimeError(msg)
        logger.info(f"🚀 Kronos 预测启动 | {len(tickers)} 只 | date={date}")
        results = self.kronos.predict_batch(tickers, date)
        if output:
            self.kronos.save_results(results, output)
        return results

    def _run_committee(
        self, research, job_id: str, date: str, ta_results: list, kronos_results: list
    ) -> None:
        """对每只 TA 分析过的股票运行 Investment Committee 审议。"""
        orchestrator = CommitteeOrchestrator(research)
        orchestrator.run(job_id, date, ta_results, kronos_results)

    @staticmethod
    @staticmethod
    def _index_ta_raw_reports(research, job_id: str, ta_results: list, raw_paths: dict) -> None:
        """索引 TA 原始报告文件到 research database。"""
        indexer = ReportIndexer(research)
        indexer.index(job_id, ta_results, raw_paths)
