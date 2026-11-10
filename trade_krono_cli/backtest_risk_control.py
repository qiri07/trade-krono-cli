#!/usr/bin/env python3
"""最大回撤控制模块。

基于 RD-Agent-Work/engine/risk_control.py 移植，适配 trade-krono-cli 回测流水线。

策略：
  - 回撤 < 10%  -> 正常仓位（100%）
  - 回撤 10~15% -> 减仓至 60%
  - 回撤 15~20% -> 减仓至 30%
  - 回撤 >= 20% -> 空仓（0%），等待恢复
  - 回撤恢复至 < 5%  -> 恢复正常仓位
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class DrawdownState:
    """回撤状态快照。"""

    peak_value: float = 0.0  # 历史最高净值
    current_value: float = 0.0  # 当前净值
    drawdown_pct: float = 0.0  # 当前回撤百分比
    max_drawdown_pct: float = 0.0  # 历史最大回撤
    is_in_cash: bool = False  # 是否处于空仓状态
    recovery_from: float = 0.0  # 开始恢复的净值（用于恢复判定）
    history: list[dict] = field(default_factory=list)  # 历史记录


class MaxDrawdownControl:
    """最大回撤控制器。

    在回测过程中实时跟踪最大回撤，动态调整仓位敞口。

    用法：
        control = MaxDrawdownControl(max_dd_threshold=0.20, recovery_threshold=0.05)
        for daily_value in daily_values:
            control.update(daily_value)
            exposure = control.get_exposure()  # 返回 [0.0, 1.0]
    """

    # 回撤阈值档位（回撤下限, 仓位上限）
    _TIER_THRESHOLDS: list[tuple[float, float]] = [
        (0.00, 1.00),  # 回撤 0%~10%   -> 100% 仓位
        (0.10, 0.60),  # 回撤 10%~15%  -> 60% 仓位
        (0.15, 0.30),  # 回撤 15%~20%  -> 30% 仓位
        (0.20, 0.00),  # 回撤 >= 20%   -> 0% 仓位（空仓）
    ]

    def __init__(
        self,
        max_dd_threshold: float = 0.20,
        recovery_threshold: float = 0.05,
        init_peak_value: float = 1_000_000.0,
    ) -> None:
        """初始化最大回撤控制器。

        Parameters
        ----------
        max_dd_threshold : float
            触发空仓的最大回撤阈值（默认 20%）。
        recovery_threshold : float
            从低谷恢复到多少以内恢复仓位（默认 5%）。
        init_peak_value : float
            初始峰值净值。
        """
        self.max_dd_threshold = max_dd_threshold
        self.recovery_threshold = recovery_threshold
        # peak_value 初始为 0，首次 update 时以首个值为基准，避免虚假回撤
        self._state = DrawdownState(
            peak_value=0.0,
            current_value=init_peak_value,
        )
        self._history: list[dict] = []

    @property
    def state(self) -> DrawdownState:
        """返回当前回撤状态。"""
        return self._state

    def update(self, current_value: float) -> dict:
        """更新回撤状态。

        Parameters
        ----------
        current_value : float
            当日净值（总资产）。

        Returns
        -------
        dict
            状态快照，包含 current_value, peak_value, drawdown_pct,
            max_drawdown_pct, is_in_cash 等字段。
        """
        self._state.current_value = current_value

        # 首次调用时，以当前值作为峰值基准，避免首次计算出虚假回撤
        if self._state.peak_value == 0.0:
            self._state.peak_value = current_value
            self._state.drawdown_pct = 0.0
            snapshot = {
                "current_value": current_value,
                "peak_value": self._state.peak_value,
                "drawdown_pct": 0.0,
                "max_drawdown_pct": 0.0,
                "is_in_cash": False,
            }
            self._history.append(snapshot)
            return snapshot

        # 更新峰值
        if current_value > self._state.peak_value:
            self._state.peak_value = current_value
            self._state.is_in_cash = False  # 创新高，退出空仓

        # 计算回撤
        dd = (self._state.peak_value - current_value) / self._state.peak_value
        self._state.drawdown_pct = dd

        # 更新历史最大回撤
        if dd > self._state.max_drawdown_pct:
            self._state.max_drawdown_pct = dd

        # 判断是否进入空仓
        if dd >= self.max_dd_threshold and not self._state.is_in_cash:
            self._state.is_in_cash = True
            self._state.recovery_from = current_value
            logger.warning(
                "🛑 触发空仓: 回撤 %.2f%% >= 阈值 %.0f%%",
                dd * 100,
                self.max_dd_threshold * 100,
            )

        # 判断是否恢复
        if (
            self._state.is_in_cash
            and self._state.recovery_from > 0
            and (self._state.peak_value - current_value) / self._state.peak_value
            < self.recovery_threshold
        ):
            self._state.is_in_cash = False
            logger.info(
                "✅ 恢复仓位: 回撤 %.2f%% < 恢复阈值 %.0f%%",
                dd * 100,
                self.recovery_threshold * 100,
            )

        snapshot = {
            "current_value": current_value,
            "peak_value": self._state.peak_value,
            "drawdown_pct": dd,
            "max_drawdown_pct": self._state.max_drawdown_pct,
            "is_in_cash": self._state.is_in_cash,
        }
        self._history.append(snapshot)
        return snapshot

    def get_exposure(self) -> float:
        """获取当前建议仓位比例 [0.0, 1.0]。

        Returns
        -------
        float
            仓位比例：0.0=空仓, 1.0=满仓。
        """
        dd = self._state.drawdown_pct

        if self._state.is_in_cash:
            return 0.0

        if dd < 0.10:
            return 1.0
        elif dd < 0.15:
            # 10%->15% 线性降至 60%
            return 1.0 - (dd - 0.10) / 0.05 * 0.40
        elif dd < 0.20:
            # 15%->20% 线性降至 0%
            return 0.60 - (dd - 0.15) / 0.05 * 0.60
        else:
            return 0.0

    def get_summary(self) -> dict:
        """获取回撤统计摘要。"""
        return {
            "peak_value": self._state.peak_value,
            "current_value": self._state.current_value,
            "drawdown_pct": self._state.drawdown_pct,
            "max_drawdown_pct": self._state.max_drawdown_pct,
            "is_in_cash": self._state.is_in_cash,
            "exposure": self.get_exposure(),
            "n_updates": len(self._history),
        }

    def reset(self, initial_value: float = 1_000_000.0) -> None:
        """重置限流器状态。"""
        self._state = DrawdownState(peak_value=initial_value, current_value=initial_value)
        self._history.clear()
