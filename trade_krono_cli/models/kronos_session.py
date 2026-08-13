"""
kronos_session — Kronos 模型常驻会话。

在单次 pipeline 调用内只加载一次模型，多次 symbol 共享同一个
_predictor 实例，避免重复 GPU/CPU 初始化开销。
"""
from __future__ import annotations

from typing import Optional

from loguru import logger
from trade_krono_cli.kronos_runner import KronosRunner, KronosForecastResult


class KronosSession:
    """
    Kronos 模型常驻会话。

    包装 KronosRunner，在 predict_batch 期间保持 _predictor 实例不变。
    默认行为（KronosRunner 本身已是单例）下直接委托，
    但在测试场景可提供 mock 预测器。
    """

    def __init__(
        self,
        runner: Optional[KronosRunner] = None,
        no_cache: bool = False,
        sample_count: Optional[int] = None,
    ):
        self._runner = runner or KronosRunner(
            no_cache=no_cache,
            sample_count=sample_count,
        )
        self._initialized = False

    @property
    def is_loaded(self) -> bool:
        """模型是否已加载到内存。"""
        return self._runner._adapter.predictor is not None

    @property
    def runner(self) -> KronosRunner:
        """返回底层 KronosRunner 实例。"""
        return self._runner

    def ensure_loaded(self) -> None:
        """确保模型已加载（懒加载）。"""
        if not self.is_loaded:
            logger.info("🧠 KronosSession: 首次加载模型...")
            self._runner._load()
            self._initialized = True
            logger.info("✅ KronosSession: 模型已就绪")

    def predict_batch(
        self,
        tickers: list[str],
        eval_date: str,
        stop_on_error: bool = False,
    ) -> list[KronosForecastResult]:
        """委托给底层 runner 的 predict_batch。"""
        self.ensure_loaded()
        return self._runner.predict_batch(tickers, eval_date, stop_on_error=stop_on_error)

    def predict_one(
        self, ticker: str, eval_date: str
    ) -> KronosForecastResult:
        """委托给底层 runner 的 predict_one。"""
        self.ensure_loaded()
        return self._runner.predict_one(ticker, eval_date)

    def save_results(self, results: list[KronosForecastResult], path: str) -> str:
        """保存预测结果到文件。"""
        return self._runner.save_results(results, path)
