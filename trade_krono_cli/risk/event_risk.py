"""Event Risk — 事件驱动风险。

基于短期/长期波动率比值的异常检测。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from trade_krono_cli.risk.models import event_risk_score as _event_risk_score

if TYPE_CHECKING:
    import pandas as pd


def calc_event_risk(
    close: pd.Series,
    short_window: int = 10,
    long_window: int = 60,
) -> float:
    """计算事件风险分（0-100）。

    短期波动率 / 长期波动率 >> 1 表示近期波动异常加剧。

    Parameters
    ----------
    close        : 收盘价序列
    short_window : 短期窗口（默认 10 日）
    long_window  : 长期窗口（默认 60 日）

    Returns
    -------
    float : 事件风险分 0-100

    """
    return _event_risk_score(close, short_window, long_window)
