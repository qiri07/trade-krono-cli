"""
ta_session — TradingAgents 模型常驻会话。

在单次 pipeline 调用内复用同一个 graph 实例，避免重复初始化。
"""
from __future__ import annotations

from typing import Optional

from loguru import logger
from trade_krono_cli.ta_runner import TradingAgentsRunner, StockAnalysisResult


class TASession:
    """
    TradingAgents 模型常驻会话。

    包装 TradingAgentsRunner，确保 graph 实例在多次分析间复用。
    """

    def __init__(
        self,
        runner: Optional[TradingAgentsRunner] = None,
        no_cache: bool = False,
    ):
        self._runner = runner or TradingAgentsRunner(no_cache=no_cache)
        self._initialized = False

    @property
    def is_loaded(self) -> bool:
        """graph 是否已初始化。"""
        return self._runner._graph is not None

    @property
    def runner(self) -> TradingAgentsRunner:
        """返回底层 runner 实例。"""
        return self._runner

    def ensure_loaded(self) -> None:
        """确保 graph 已初始化。"""
        if not self.is_loaded:
            logger.info("🤖 TASession: 首次初始化 TradingAgents graph...")
            # 调用 _get_graph() 触发懒加载
            self._runner._get_graph()
            self._initialized = True
            logger.info("✅ TASession: graph 已就绪")

    def analyze_batch(
        self,
        tickers: list[str],
        date: str,
        progress_cb=None,
    ) -> list[StockAnalysisResult]:
        """委托给底层 runner 的 analyze_batch。"""
        self.ensure_loaded()
        return self._runner.analyze_batch(tickers, date, progress_cb=progress_cb)

    def analyze_one(
        self, ticker: str, date: str
    ) -> StockAnalysisResult:
        """委托给底层 runner 的 analyze_one。"""
        self.ensure_loaded()
        return self._runner.analyze_one(ticker, date)

    def save_results(self, results: list[StockAnalysisResult], path: str) -> str:
        """保存分析结果到文件。"""
        return self._runner.save_results(results, path)

    def save_raw_reports(
        self, results: list[StockAnalysisResult], date: str,
    ) -> dict[str, str]:
        """保存完整原始报告。"""
        return self._runner.save_raw_reports(results, date)
