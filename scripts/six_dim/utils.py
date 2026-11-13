"""six_dim 选股 — 工具函数。

包含 _clamp / _pct_rank / _recent_quarters 等通用辅助函数。
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from datetime import date


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    """把数值裁剪到 [lo, hi] 区间。"""
    return max(lo, min(hi, value))


def _pct_rank(series: Iterable[float], value: float) -> float:
    """计算 value 在序列中的百分位（0~100，越小越便宜）。"""
    vals = [v for v in series if v is not None and v > 0 and math.isfinite(v)]
    if not vals or value is None or value <= 0:
        return 100.0
    below = sum(1 for v in vals if v <= value)
    return below / len(vals) * 100.0


def _recent_quarters(today: date, n: int = 6) -> list[str]:
    """返回最近 n 个已披露季度的季度末日期字符串（YYYYMMDD，降序）。

    季度末：3/6/9/12 月末，披露滞后一个季度，故从上一季度末开始回退。
    """
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


def _analysis_year(date_str: str | None) -> int:
    """返回分析日期对应的最近已披露年报年份。

    年报在次年 5 月前披露：11 月看 Y-1 年报，3 月看 Y-2 年报。
    避免硬编码年份导致跨年后静默产出 0 只。
    """
    ref = date_str or date.today().isoformat()
    try:
        d = date.fromisoformat(ref)
    except ValueError:
        return date.today().year
    return d.year - 1 if d.month >= 5 else d.year - 2
