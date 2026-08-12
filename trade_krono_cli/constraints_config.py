"""
A 股交易约束配置。

管理涨跌停、T+1、ST 过滤、交易成本等参数的默认值与自定义覆盖。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=False)
class ConstraintConfig:
    """A 股交易约束参数。"""

    # ── 涨跌停检测 ──────────────────────────────────────────
    enable_limit_check: bool = True
    """是否启用涨跌停价格检测。"""

    sse_limit_pct: float = 10.0
    """主板（上交所 sh.）涨跌停幅度百分比。"""

    szse_limit_pct: float = 20.0
    """创业板/科创板（深交所 sz.）涨跌停幅度百分比。"""

    # ── T+1 结算 ────────────────────────────────────────────
    enable_t1: bool = True
    """是否启用 T+1 买入锁定约束。"""

    # ── ST 过滤 ─────────────────────────────────────────────
    enable_st_filter: bool = True
    """是否过滤 ST / *ST 标的。"""

    # ── 交易成本 ────────────────────────────────────────────
    commission_bps: float = 3.0
    """佣金（万分之三）。"""

    slippage_bps: float = 5.0
    """滑点（5bp）。"""

    stamp_duty_bps: float = 1.0
    """印花税（卖出时 1bp，万分之一）。"""

    # ── 复权因子 ────────────────────────────────────────────
    adjustflag: str = "1"
    """
    baostock 复权因子：
      "0" = 不复权
      "1" = 前复权（默认，更适合技术分析）
      "2" = 后复权
    """

    # ── 成本计算开关 ────────────────────────────────────────
    enable_cost_model: bool = True
    """是否从收益中扣除交易成本。"""

    def total_roundtrip_bps(self) -> float:
        """双边（买+卖）总成本（bps）。"""
        buy = self.commission_bps + self.slippage_bps
        sell = self.commission_bps + self.slippage_bps + self.stamp_duty_bps
        return buy + sell

    def sell_cost_bps(self) -> float:
        """单次卖出的成本（bps）。"""
        return self.commission_bps + self.slippage_bps + self.stamp_duty_bps

    def buy_cost_bps(self) -> float:
        """单次买入的成本（bps）。"""
        return self.commission_bps + self.slippage_bps

    def apply_cost(self, gross_return_pct: float) -> float:
        """
        将毛收益（%）转换为扣减买入成本后的净收益（%）。

        注意：实际完整成本需要知道买卖双方，此方法仅扣减买入侧。
        完整双边成本请用 :meth:`apply_roundtrip_cost`。
        """
        if not self.enable_cost_model:
            return gross_return_pct
        return gross_return_pct - self.buy_cost_bps() / 100.0

    def apply_roundtrip_cost(self, gross_return_pct: float) -> float:
        """
        双边交易（买+卖）后的净收益（%）。
        """
        if not self.enable_cost_model:
            return gross_return_pct
        return gross_return_pct - self.total_roundtrip_bps() / 100.0
