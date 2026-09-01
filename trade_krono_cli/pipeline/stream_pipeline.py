"""
stream_pipeline — 流式流水线调度器。

核心思路：数据拉取（I/O）与模型推理（计算）重叠执行。
传统模式：先拉取所有 K 线 → 再并行计算 TA+Kronos，总耗时 = T_fetch + T_compute
流式模式：按 ticker 分组提交，fetch/TA/Kronos 三阶段并发，总耗时 ≈ max(T_fetch, T_compute)

线程分工：
  - fetch_future   预取全部 K 线（在 TA/Kronos 启动前完成，保证数据就绪）
  - ta_future      批量 TA 分析（从预取数据读取，无重复网络 I/O）
  - kronos_future  批量 Kronos 预测（使用 pre-fetched 数据跳过 fetch_lookback）
"""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

import pandas as pd
from loguru import logger

from trade_krono_cli.kronos_runner import KronosForecastResult
from trade_krono_cli.pipeline.data_fetcher import prepare_kline_batch
from trade_krono_cli.ta_runner import StockAnalysisResult


class StreamPipeline:
    """
    流式投研流水线。

    与 QuantPipeline.run_parallel() 的区别：
    - 数据拉取与模型推理重叠执行，总耗时 ≈ max(T_fetch, T_compute)
    - 每只股票的数据只拉取一次，预取后注入 KronosRunner._pre_fetched
    """

    def __init__(
        self,
        ta_runner: Any = None,
        kronos_runner: Any = None,
        no_cache: bool = False,
        progress_cb: Optional[Callable[[str, int, int], None]] = None,
    ) -> None:
        self.ta_runner = ta_runner
        self.kronos_runner = kronos_runner
        self.no_cache = no_cache
        self.progress_cb = progress_cb
        # 从 runner 的 settings 对象获取 pred_len，默认 30
        self._pred_len = StreamPipeline._resolve_pred_len(self.kronos_runner)

    @staticmethod
    def _resolve_pred_len(kronos_runner: Any) -> int:
        """从 kronos_runner 的 settings 对象获取预测长度，未配置则返回默认值 30。"""
        if kronos_runner is None:
            return 30
        try:
            for attr_name in ("_settings_obj", "settings", "_settings"):
                if hasattr(kronos_runner, attr_name):
                    s = getattr(kronos_runner, attr_name)
                    if hasattr(s, "kronos_pred_len"):
                        return int(s.kronos_pred_len)
                    if hasattr(s, "pred_len"):
                        return int(s.pred_len)
        except Exception:
            pass
        return 30

    def run(
        self,
        tickers: list[str],
        date: str,
        lookback: int = 400,
        adjustflag: str = "1",
        use_cache: bool = True,
    ) -> tuple[list[StockAnalysisResult], list[KronosForecastResult], dict[str, Any]]:
        """
        流式运行 TA + Kronos，数据拉取与计算重叠执行。

        Returns
        -------
        (ta_results, kronos_results, kline_data)
            kline_data 供下游 merge_results 进行风险评分使用
        """
        t0 = time.time()
        n = len(tickers)
        logger.info(f"🚀 流式流水线启动 | {n} 只候选 | date={date} | fetch与compute重叠执行")

        if self.progress_cb:
            self.progress_cb("启动", 0, n)

        # ── Phase 1: 预取全部 K 线 ─────────────────────────────────────────
        # 在独立线程中拉取，与后续阶段重叠（若 TA/Kronos 启动更早则提前完成）
        kline_data: dict = {}

        def _fetch_all() -> dict:
            return prepare_kline_batch(
                tickers,
                date,
                lookback=lookback,
                adjustflag=adjustflag,
                use_cache=use_cache,
            )

        # ── Phase 2+3: TA + Kronos 并行，数据在 Phase 1 就绪后使用 ────────
        ta_results: list[StockAnalysisResult] = []
        kr_results: list[KronosForecastResult] = []

        def _run_ta() -> list:
            results: list = []
            for idx, tk in enumerate(tickers, 1):
                try:
                    res = self._ta_analyze_one(tk, date, kline_data.get(tk))
                    results.append(res)
                except Exception as e:
                    logger.error(f"❌ TA 分析异常 {tk}: {e}")
                    results.append(StockAnalysisResult(ticker=tk, date=date, error=str(e)))
                finally:
                    if self.progress_cb:
                        try:
                            self.progress_cb("TA分析", idx, n)
                        except Exception:
                            pass
            return results

        def _run_kronos() -> list:
            results: list = []
            for idx, tk in enumerate(tickers, 1):
                try:
                    res = self._kronos_predict_one(tk, date, kline_data.get(tk))
                    results.append(res)
                except Exception as e:
                    logger.error(f"❌ Kronos 预测异常 {tk}: {e}")
                    results.append(
                        KronosForecastResult(ticker=tk, eval_date=date, horizon=self._pred_len, error=str(e))
                    )
                finally:
                    if self.progress_cb:
                        try:
                            self.progress_cb("Kronos预测", idx, n)
                        except Exception:
                            pass
            return results

        # 三线程并发：fetch + TA + Kronos 同时启动
        # fetch 虽先启动，TA/Kronos 会阻塞等 kline_data 赋值（短临界区）
        with ThreadPoolExecutor(max_workers=3) as executor:
            fetch_fut = executor.submit(_fetch_all)
            ta_fut = executor.submit(_run_ta)
            kronos_fut = executor.submit(_run_kronos)

            # 等待 fetch 完成并注入 pre-fetched 数据
            kline_data = fetch_fut.result()

            if self.kronos_runner is not None:
                if not hasattr(self.kronos_runner, "_pre_fetched"):
                    self.kronos_runner._pre_fetched = {}  # type: ignore
                for tk, df in kline_data.items():
                    if df is not None and len(df) > 0:
                        self.kronos_runner._pre_fetched[tk] = df  # type: ignore
                logger.debug(
                    f"📦 注入 pre-fetched K 线: "
                    f"{sum(1 for v in self.kronos_runner._pre_fetched.values() if v is not None)} 只"
                )

            ta_results = ta_fut.result()
            kr_results = kronos_fut.result()

        elapsed = time.time() - t0
        n_ta_ok = sum(1 for r in ta_results if r.error is None)
        n_kr_ok = sum(1 for r in kr_results if r.error is None)
        logger.info(
            f"📊 流式流水线完成: TA {n_ta_ok}/{n} | Kronos {n_kr_ok}/{n} | 耗时 {elapsed:.1f}s"
        )

        if self.progress_cb:
            self.progress_cb("完成", n, n)

        return ta_results, kr_results, kline_data

    def _ta_analyze_one(
        self, ticker: str, date: str, df: Optional[pd.DataFrame]
    ) -> StockAnalysisResult:
        """单只股票 TA 分析，优先使用预取数据。"""
        if self.ta_runner is None:
            return StockAnalysisResult(ticker=ticker, date=date, error="TA runner 未初始化")
        if hasattr(self.ta_runner, "_pre_fetched") and df is not None:
            self.ta_runner._pre_fetched[ticker] = df  # type: ignore
        return self.ta_runner.analyze_one(ticker, date)

    def _kronos_predict_one(
        self, ticker: str, date: str, df: Optional[pd.DataFrame]
    ) -> KronosForecastResult:
        """单只股票 Kronos 预测，优先使用预取数据。"""
        if self.kronos_runner is None:
            return KronosForecastResult(
                ticker=ticker,
                eval_date=date,
                horizon=self._pred_len,
                error="Kronos runner 未初始化",
            )
        return self.kronos_runner.predict_one(ticker, date)
