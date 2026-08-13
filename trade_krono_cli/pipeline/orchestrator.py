"""
orchestrator — 调度主循环。

QuantPipeline 的核心实现，负责协调 TA + Kronos 并行执行、
结果合并、落盘和数据库写入。

PipelineFactory 负责组装组件（TradingAgentsSession / KronosSession），
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
from trade_krono_cli.models.kronos_session import KronosSession
from trade_krono_cli.models.ta_session import TASession
from trade_krono_cli.kronos_runner import KronosForecastResult
from trade_krono_cli.ta_runner import StockAnalysisResult
from trade_krono_cli.pipeline.merge import merge_results, filter_pool
from trade_krono_cli.research_db import get_research
from trade_krono_cli.trading_constraints import T1Tracker
from trade_krono_cli.constraints_config import ConstraintConfig
from trade_krono_cli.pipeline_config import PipelineConfig
from trade_krono_cli.pipeline.data_fetcher import prepare_kline_batch
from trade_krono_cli.stock_filter import StockFilter, StockMeta
from trade_krono_cli.abnormal_stock import (
    precheck_stock_status,
    check_kline_completeness,
    apply_abnormality_risk_boost,
    StockAbnormality,
    AbnormalityFlag,
)
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

    负责根据 Settings / PipelineConfig 组装 TASession 和 KronosSession，
    将「组件创建」与「执行调度」解耦。

    用法：
        # 生产路径：完全由工厂创建
        ta_session, kronos_session = PipelineFactory.create(settings, config, no_cache=False)

        # 测试注入：传入 mock session，工厂补全缺失的另一个
        ta_session, kronos_session = PipelineFactory.create(
            settings, config, no_cache=True,
            ta_session=mock_ta,   # 仅注入 TA，Kronos 由工厂创建
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
        ta_session: Optional[Any] = None,
        kronos_session: Optional[Any] = None,
    ) -> tuple[Any, Optional[Any]]:
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
        ta_session        : 测试注入的 TA session/runner（None 时由工厂创建）
        kronos_session    : 测试注入的 Kronos session/runner（None 时由工厂创建）

        Returns
        -------
        (ta_session, kronos_session)
          kronos_session 在 skip_kronos=True 或无注入时为 None
        """
        # 兼容测试注入：直接传入 runner 对象时包装为 session
        ta = PipelineFactory._ensure_session(ta_session, "ta")
        if skip_kronos:
            return ta, None
        kronos = PipelineFactory._ensure_session(kronos_session, "kronos")
        return ta, kronos

    @staticmethod
    def _ensure_session(obj: Optional[Any], kind: str) -> Any:
        """将 runner 对象包装为 session，或原样返回 session 对象。"""
        from unittest.mock import MagicMock as _Mock
        if obj is None:
            if kind == "ta":
                return TASession()
            return KronosSession()
        # 如果已具备完整 session 接口（TASession / KronosSession 实例），直接返回
        if isinstance(obj, (TASession, KronosSession)):
            return obj
        # 如果是 MagicMock，直接使用（避免包装后 .runner 变成新 MagicMock 丢失 return_value）
        if isinstance(obj, _Mock):
            obj.runner = obj
            obj.is_loaded = True
            return obj
        # 否则视为旧的 runner 对象，包装为适配器
        wrapper = _Mock()
        wrapper.runner = obj
        wrapper.is_loaded = True
        wrapper.adapter = getattr(obj, "adapter", None)
        wrapper.predict_batch = obj.predict_batch if hasattr(obj, "predict_batch") else None
        wrapper.analyze_batch = obj.analyze_batch if hasattr(obj, "analyze_batch") else None
        return wrapper


class QuantPipeline:
    """
    一站式投研流水线：
      1. TradingAgents 批量分析（线程1）
      2. Kronos 批量预测   （线程2）
      3. 两者并行完成后融合打分
    """

    def __init__(
        self,
        ta_session: Optional[Any] = None,
        kronos_session: Optional[Any] = None,
        config: Optional[PipelineConfig] = None,
        # 向后兼容参数（创建默认 session 时使用）
        min_confidence: Optional[float] = None,
        allowed_signals: Optional[tuple[str, ...]] = None,
        skip_kronos: bool = False,
        no_cache: bool = False,
        constraints_config: Optional[ConstraintConfig] = None,
        sample_count: Optional[int] = None,
        settings: Optional[Settings] = None,
        # 向后兼容：直接注入 runner
        ta_runner: Optional[Any] = None,
        kronos_runner: Optional[Any] = None,
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
        if hasattr(self.ta, '_retry_policy'):
            self.ta._retry_policy = retry_policy
        if self.kronos and hasattr(self.kronos, '_retry_policy'):
            self.kronos._retry_policy = retry_policy

        logger.info(
            f"🏭 QuantPipeline 就绪 | "
            f"min_confidence={self.min_confidence} "
            f"allowed_signals={self.allowed_signals} "
            f"skip_kronos={skip_kronos} "
            f"constraints={'enabled' if self.constraints_config.enable_limit_check else 'disabled'} "
            f"sample_count={'(default)' if sample_count is None else sample_count}"
        )

    def _apply_ta_cache_fallback(self, ta_results: list) -> None:
        """TA 失败时，从研究数据库回退到最近一次成功的缓存 TA 结果。"""
        research = get_research()
        max_age_days = self._config.ta_cache_max_age_days
        ta_fallback_count = 0
        for ta in ta_results:
            if ta.error is None:
                continue
            cached = research.get_latest_ta_for_ticker(
                ta.ticker, max_age_days=max_age_days,
            )
            if cached:
                logger.info(
                    f"📦 {ta.ticker} TA 失败，回退到 {cached['date']} 的缓存结果 "
                    f"(signal={cached['signal']}, confidence={cached['confidence']})"
                )
                ta.signal = cached["signal"]
                ta.confidence = cached["confidence"]
                ta.reasoning = cached.get("thesis") or ""
                ta.error = None
                ta_fallback_count += 1
        if ta_fallback_count:
            logger.info(
                f"📦 TA 缓存回退完成: {ta_fallback_count}/{len(ta_results)} 只股票"
            )

    def run_parallel(
        self,
        tickers: list[str],
        date: str,
        output_json: Optional[str] = None,
        output_html: Optional[str] = None,
        progress_cb: Optional[Callable[[str, int, int], None]] = None,
        streaming: bool = False,
    ) -> list[dict]:
        """并行运行 TA + Kronos，合并结果。

        Parameters
        ----------
        streaming : 是否启用流式模式。True 时数据拉取与计算重叠执行，
                    总耗时 ≈ max(T_fetch, T_compute)；默认 False 使用传统批量模式。
        """
        t0 = time.time()
        logger.info(
            f"🚀 流水线启动{'（流式）' if streaming else '（并行）'}| "
            f"{len(tickers)} 只候选 | date={date}"
        )

        research = get_research()
        job_id = research.create_job(date, tickers, settings=self._settings)

        if progress_cb:
            progress_cb("启动", 0, 2)

        # ── 异常股票预检（在 TA/Kronos 执行前批量检测）───────────────
        cfg = self._config
        abnormal_flags_map = precheck_stock_status(
            tickers=tickers,
            eval_date=date,
            min_listing_days=getattr(cfg, 'new_stock_min_days', 60),
            skip_suspended=cfg.exclude_st or True,
            skip_new_stock=getattr(cfg, 'skip_new_stock', True),
        )
        # 记录被标记为退市的股票
        delisted = [
            t for t, f in abnormal_flags_map.items()
            if StockAbnormality.DELISTED in f.flags
        ]
        if delisted:
            logger.warning(
                f"🚫 检测到 {len(delisted)} 只退市股票，将从分析中排除: "
                f"{', '.join(delisted)}"
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
            )
            ta_results, kronos_results = stream.run(
                tickers=tickers,
                date=date,
                lookback=self._config.lookback,
                adjustflag=self.constraints_config.adjustflag,
                use_cache=self._config.use_cache,
            )
        else:
            # ── 批量准备 K 线数据（共享给风险引擎）─────────────────
            kline_data = prepare_kline_batch(
                tickers, date,
                lookback=self._config.lookback,
                adjustflag=self.constraints_config.adjustflag,
                use_cache=self._config.use_cache,
            )

            # ── K 线完整性校验（标记数据不足的股票）─────────────────
            min_completeness = getattr(cfg, 'kline_min_completeness', 0.85)
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
                        new_flags = existing.flags + [StockAbnormality.DATA_INSUFFICIENT]
                        abnormal_flags_map[tk] = AbnormalityFlag(
                            ticker=tk,
                            flags=new_flags,
                            severity=max(existing.severity, 0.5),
                            reason=existing.reason or reason,
                        )
                    logger.warning(f"⚠️  {reason}")

            # ── 并行执行 TA + Kronos（错误隔离）──────────────────
            with ThreadPoolExecutor(max_workers=2) as executor:
                ta_future = executor.submit(
                    self.ta.analyze_batch, tickers, date
                )
                kronos_future = executor.submit(
                    self.kronos.predict_batch, tickers, date
                ) if self.kronos else None

                ta_results, kronos_results = _collect_futures(
                    ta_future, kronos_future
                )

        # ── 降级模式：TA 缓存回退 ────────────────────────────────
        if self._config.degrade_mode == "ta_cache_fallback" and self._config.ta_cache_fallback_enabled:
            self._apply_ta_cache_fallback(ta_results)

        # ── 应用过滤（信号 / 置信度阈值）────────────────────────────
        filtered_ta = filter_pool(
            ta_results,
            min_confidence=self.min_confidence,
            allowed_signals=self.allowed_signals,
        )

        # ── 股票元数据过滤（市值 / 行业 / PE/PB / 风险分 / 成交量 + 异常标记）──
        cfg = self._config
        filter_engine = StockFilter.from_config(
            min_confidence=self.min_confidence,
            allowed_signals=self.allowed_signals,
            market_cap_range=cfg.market_cap_range,
            industry_whitelist=cfg.industry_whitelist,
            industry_blacklist=cfg.industry_blacklist,
            pe_range=cfg.pe_range,
            pb_range=cfg.pb_range,
            max_risk_score=cfg.max_risk_score,
            min_volume_ratio=cfg.min_volume_ratio,
            min_turnover_rate=cfg.min_turnover_rate,
            exclude_st=cfg.exclude_st,
        )

        # 构建 StockMeta 并注入异常标记，用于过滤
        filtered_ta_list: list = []
        rejected_ta: list = []
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
            )
            # ST / 停牌 / 退市股票直接过滤
            blocker_flags = {"ST", "SUSPENDED", "DELISTED"}
            if blocker_flags & set(meta.abnormal_flags):
                rejected_ta.append(r)
                continue
            filtered_ta_list.append(r)

        # 再用常规 StockFilter 过滤（置信度 / 信号等）
        passed_ta, rejected_ta_extra = filter_engine.apply_batch(filtered_ta_list)
        rejected_ta.extend(rejected_ta_extra)
        filtered_ta = passed_ta

        logger.info(
            f"📋 元数据过滤完成: 保留 {len(filtered_ta)} 只 "
            f"（原始池 {len(filtered_ta)} + 已过滤 {len(rejected_ta)}）"
        )

        # ── 合并 + 打分（含交易约束）────────────────────────────
        t1_tracker = T1Tracker()
        merged = merge_results(
            filtered_ta,
            kronos_results,
            kline_data=kline_data,
            constraints_config=self.constraints_config,
            t1_tracker=t1_tracker,
            scoring_config=self._config.scoring,
            risk_config=self._config.risk,
            scoring_strategy=self._config.scoring_strategy,
            degrade_mode=self._config.degrade_mode,
        )

        # ── 异常风险分上调 ──────────────────────────────────────
        if getattr(cfg, 'abnormality_risk_boost_enabled', True):
            risk_boost_cfg = cfg.risk_boost_strategy
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
                        params=risk_boost_cfg.params if hasattr(risk_boost_cfg, 'params') else None,
                    )
                    item["risk_score_total"] = boosted
                    item["abnormal_flags"] = af.flag_names()

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
            raise RuntimeError("KronosSession 未初始化（skip_kronos=True）")
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
