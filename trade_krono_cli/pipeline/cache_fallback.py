"""pipeline.cache_fallback — TA 缓存回退逻辑。

职责（从 QuantPipeline._apply_ta_cache_fallback 提取）：
  · TA 分析失败时，从研究数据库回退到最近一次成功的缓存 TA 结果
  · 兼容 StockAnalysisResult 与 TAAnalysis 的字段差异
"""

from __future__ import annotations

from typing import Any

from loguru import logger


def apply_ta_cache_fallback(
    ta_results: list[Any],
    max_age_days: int,
    research: Any,
) -> int:
    """TA 失败时，从研究数据库回退到最近一次成功的缓存 TA 结果。

    Parameters
    ----------
    ta_results : list[Any]
        TA 分析结果列表（会被原地修改，替换失败项）。
    max_age_days : int
        缓存结果最大天数。
    research : Any
        研究数据库实例。

    Returns
    -------
    int
        回退成功的股票数量。
    """
    ta_fallback_count = 0
    for idx, ta in enumerate(ta_results):
        if ta.error is None:
            continue
        cached = research.get_latest_ta_for_ticker(
            ta.ticker,
            max_age_days=max_age_days,
        )
        if cached:
            logger.info(
                f"📦 {ta.ticker} TA 失败，回退到 {cached['date']} 的缓存结果 "
                f"(signal={cached['signal']}, confidence={cached['confidence']})",
            )
            # TAAnalysis 是 frozen dataclass，必须创建新实例替代原地修改
            # StockAnalysisResult 用 .date，TAAnalysis 用 .eval_date，统一兼容
            from trade_krono_cli.ta_runner import StockAnalysisResult

            ta_date = getattr(ta, "eval_date", None) or str(getattr(ta, "date", ""))
            # 下游 merge.py 期望 StockAnalysisResult（有 .date / .reports）
            ta_results[idx] = StockAnalysisResult(
                ticker=ta.ticker,
                date=ta_date,
                signal=cached["signal"],
                confidence=cached["confidence"],
                reasoning=cached.get("thesis") or "",
                error=None,
                elapsed_sec=ta.elapsed_sec,
            )
            ta_fallback_count += 1

    if ta_fallback_count:
        logger.info(f"📦 TA 缓存回退完成: {ta_fallback_count}/{len(ta_results)} 只股票")
    return ta_fallback_count
