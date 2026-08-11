"""
结果合并 + 综合打分。
"""
from __future__ import annotations

from typing import Optional

from loguru import logger
from trade_krono_cli.ta_runner import StockAnalysisResult
from trade_krono_cli.kronos_runner import KronosForecastResult


# ═══════════════════════════════════════════════════════
# 综合打分
# ═══════════════════════════════════════════════════════

def default_scorer(merged: dict) -> float:
    """
    综合打分（满分 100）。
    权重：TA 置信度 40% + Kronos 涨跌幅 40% + 方向加成 20%
    """
    score = 0.0

    # TA 部分（0-40）
    ta_conf = merged.get("ta_confidence") or 0
    score += 0.4 * max(0, min(100, ta_conf))

    # Kronos 部分（0-40，涨跌幅映射到 0-100）
    chg = merged.get("kronos_change_pct") or 0
    score += 0.4 * max(0, min(100, chg + 50))  # -50%~+50% → 0~100

    # 方向加成（-20 ~ +20）
    direction = merged.get("kronos_direction")
    if direction == "UP":
        score += 0.2 * 20  # +4
    elif direction == "DOWN":
        score += 0.2 * (-20)  # -4

    return round(max(0, min(100, score)), 2)


# ═══════════════════════════════════════════════════════
# 合并函数
# ═══════════════════════════════════════════════════════

def _make_empty_merged(
    ticker: str,
    ta: Optional[StockAnalysisResult],
    kronos: Optional[KronosForecastResult],
) -> dict:
    return {
        "ticker": ticker,
        "ta_signal": ta.signal if ta else None,
        "ta_confidence": ta.confidence if ta else None,
        "ta_reasoning": (ta.reasoning or "")[:500] if ta else "",
        "ta_reports": (ta.reports or {}) if ta else {},
        "ta_error": ta.error if ta else None,
        "kronos_direction": kronos.direction if kronos else None,
        "kronos_change_pct": kronos.expected_change_pct if kronos else None,
        "kronos_volatility": kronos.volatility_proxy if kronos else None,
        "kronos_last_close": kronos.last_close if kronos else None,
        "kronos_pred_close": kronos.predicted_close_final if kronos else None,
        "kronos_confidence_band": kronos.confidence_band if kronos else None,
        "kronos_error": kronos.error if kronos else None,
        "composite_score": None,
        "forecast_dict": kronos.forecast_dict if kronos else None,
    }


def merge_results(
    ta_results: list[StockAnalysisResult],
    kronos_results: list[KronosForecastResult],
    scorer: Optional[callable] = None,
) -> list[dict]:
    """
    将 TA 分析结果和 Kronos 预测结果合并。

    Parameters
    ----------
    ta_results : TA 分析结果列表
    kronos_results : Kronos 预测结果列表
    scorer : 自定义打分函数，默认为 default_scorer

    Returns
    -------
    排序后的综合结果列表（按 composite_score 降序）
    """
    if scorer is None:
        scorer = default_scorer

    # 构建 Kronos 查找表
    kronos_map = {r.ticker: r for r in kronos_results if r.error is None}

    merged = []
    for ta in ta_results:
        kr = kronos_map.get(ta.ticker)
        item = _make_empty_merged(ta.ticker, ta, kr)
        item["composite_score"] = scorer(item)
        merged.append(item)

    # 按综合分降序
    merged.sort(key=lambda x: (x.get("composite_score") or 0), reverse=True)

    # 添加排名
    for i, item in enumerate(merged, 1):
        item["rank"] = i

    logger.info(
        f"📊 合并完成: {len(merged)} 只股票，"
        f"TA 成功 {sum(1 for r in ta_results if r.error is None)}/{len(ta_results)}, "
        f"Kronos 成功 {len(kronos_map)}/{len(kronos_results)}"
    )

    return merged


def filter_pool(
    ta_results: list[StockAnalysisResult],
    min_confidence: float = 55.0,
    allowed_signals: tuple[str, ...] = ("BUY", "HOLD"),
) -> list[dict]:
    """
    按信号 + 置信度过滤出可行股票池。
    """
    pool = []
    for r in ta_results:
        if r.error:
            continue
        if r.signal in allowed_signals and (r.confidence or 0) >= min_confidence:
            pool.append({
                "ticker": r.ticker,
                "ta_signal": r.signal,
                "ta_confidence": r.confidence,
                "ta_reasoning": (r.reasoning or "")[:300],
                "ta_reports": r.reports,
                "ta_result": r,
            })
    logger.info(f"📋 过滤后股票池: {len(pool)} 只")
    for p in pool:
        logger.info(f"   • {p['ticker']}: {p['ta_signal']} 置信度 {p['ta_confidence']}")
    return pool
