"""double_ma 选股 — 飞书卡片打印。

包含 _print_top30，用于打印 TOP 30 股票表格。
"""

from __future__ import annotations

from loguru import logger


def _print_top30(results: list[dict], *, with_roe: bool) -> None:
    """打印 TOP 30 股票表格。

    Parameters
    ----------
    results : list[dict]
        筛选结果列表
    with_roe : bool
        是否包含 ROE / 净利润 列（完整模式）
    """
    top_n = min(30, len(results))
    logger.info(f"\nTOP {top_n} 股票:")
    if with_roe:
        logger.info(
            f"{'序号':<4} {'代码':<10} {'名称':<10} {'行业':<10} "
            f"{'收盘':<8} {'PE':<6} {'ROE%':<6} {'利润亿':<6} {'日Ratio':<8} {'周Ratio':<8} {'MACD':<10}"
        )
        logger.info("-" * 110)
        for i, s in enumerate(results[:top_n], 1):
            logger.info(
                f"{i:<4} {s['ticker']:<10} {s['name'] or 'N/A':<10} {s['industry'] or 'N/A':<10} "
                f"{s['latest_close']:<8.2f} {s['pe_ttm'] or 0:<6.2f} "
                f"{s.get('roe') or 0:<6.2f} {s.get('net_profit_yi') or 0:<6.2f} "
                f"{s['daily_ratio']:<8.4f} {s['weekly_ratio']:<8.4f} {s['macd']:<10.6f}"
            )
    else:
        logger.info(
            f"{'序号':<4} {'代码':<10} {'名称':<10} {'行业':<10} "
            f"{'收盘':<8} {'PE':<6} {'日Ratio':<8} {'周Ratio':<8} {'MACD':<10}"
        )
        logger.info("-" * 90)
        for i, s in enumerate(results[:top_n], 1):
            logger.info(
                f"{i:<4} {s['ticker']:<10} {s['name'] or 'N/A':<10} {s['industry'] or 'N/A':<10} "
                f"{s['latest_close']:<8.2f} {s['pe_ttm'] or 0:<6.2f} "
                f"{s['daily_ratio']:<8.4f} {s['weekly_ratio']:<8.4f} {s['macd']:<10.6f}"
            )
