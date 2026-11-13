"""six_dim 选股 — 股东人数维度。

负责获取股东户数环比变化，并执行股东人数维度打分。
"""

from __future__ import annotations

from datetime import date

import pandas as pd
from loguru import logger

from scripts.six_dim.constants import DIM_LABELS  # noqa: F401  (供兼容导入)


def fetch_holder_counts(today: object | None = None) -> dict[str, dict[str, float]]:
    """获取东财全市场股东户数（最近披露季度）。

    Args:
        today: 分析日期（兼容旧签名，实际不使用）。

    Returns:
        ``code -> {"chg_pct": 环比变化(%), "holders": 本次户数, "avg_value": 户均市值}``。
    """
    from datetime import date

    import akshare as ak

    today_date = today if isinstance(today, date) else date.today()

    for quarter in _recent_quarters(today_date, 6):
        try:
            df = ak.stock_zh_a_gdhs(symbol=quarter)
            if df is None or df.empty:
                continue
        except (OSError, ValueError, KeyError, TypeError) as exc:
            logger.warning(f"股东户数 {quarter} 获取失败：{exc}")
            continue
        out: dict[str, dict[str, float]] = {}
        for _, row in df.iterrows():
            code = str(row.get("代码", "")).zfill(6)
            chg = row.get("股东户数-增减比例")
            holders = row.get("股东户数-本次")
            avg_value = row.get("户均持股市值")
            try:
                out[code] = {
                    "chg_pct": float(chg) if chg is not None and not pd.isna(chg) else 0.0,
                    "holders": 0.0 if holders is None or pd.isna(holders) else float(holders),
                    "avg_value": 0.0 if avg_value is None or pd.isna(avg_value) else float(avg_value),
                }
            except (TypeError, ValueError):
                continue
        logger.info(f"股东户数：{quarter} 期共 {len(out)} 只")
        return out
    logger.warning("股东户数：最近 6 个季度均未取到数据，该维度按中性 50 分处理")
    return {}


def _recent_quarters(today: date, n: int = 6) -> list[str]:
    """返回最近 n 个季度字符串（如 "20260331"），用于 akshare 查询。"""
    quarters: list[str] = []
    y, m = today.year, today.month
    for _ in range(n):
        q_month = ((m - 1) // 3) * 3
        if q_month == 0:
            q_month = 12
            y -= 1
        q_day = 31 if q_month in (3, 12) else 30
        quarters.append(f"{y}{q_month:02d}{q_day}")
        m = q_month - 2
        if m <= 0:
            m += 12
            y -= 1
    return quarters


def score_holder(info: dict[str, float] | None) -> tuple[float, str]:
    """股东人数打分：户数减少（筹码集中）加分，户数增加扣分。

    Args:
        info: :func:`fetch_holder_counts` 返回的字典。

    Returns:
        ``(0~100 分, 说明文本)``。
    """
    if info is None:
        return 50.0, "无股东户数"
    chg_pct = info.get("chg_pct", 0.0)
    if chg_pct < 0:
        score = min(100, 50 + abs(chg_pct) * 5)
        detail = f"股东户数减少 {abs(chg_pct):.1f}%，筹码明显集中"
    else:
        score = max(0, 50 - chg_pct * 5)
        detail = f"股东户数增加 {chg_pct:.1f}%"
    return round(score, 1), detail
