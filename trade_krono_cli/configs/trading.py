"""A 股交易约束配置。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=False)
class ConstraintConfig:
    """A 股交易约束参数。"""

    enable_limit_check: bool = True
    sse_limit_pct: float = 10.0
    szse_limit_pct: float = 20.0
    enable_t1: bool = True
    enable_st_filter: bool = True

    commission_bps: float = 3.0
    slippage_bps: float = 5.0
    stamp_duty_bps: float = 1.0
    adjustflag: str = "1"
    enable_cost_model: bool = True

    def merge(self, **overrides) -> "ConstraintConfig":
        current = {k: getattr(self, k) for k in self.__dataclass_fields__}
        current.update(overrides)
        return ConstraintConfig(**current)

    def validate(self) -> list[str]:
        errors: list[str] = []
        for name, val in [
            ("commission_bps", self.commission_bps),
            ("slippage_bps", self.slippage_bps),
            ("stamp_duty_bps", self.stamp_duty_bps),
        ]:
            if val < 0:
                errors.append(f"{name}={val} 不能为负")
        return errors

    # ── 成本计算（保留在原位置的方法）────────────────────────────

    def total_roundtrip_bps(self) -> float:
        buy = self.commission_bps + self.slippage_bps
        sell = self.commission_bps + self.slippage_bps + self.stamp_duty_bps
        return buy + sell

    def sell_cost_bps(self) -> float:
        return self.commission_bps + self.slippage_bps + self.stamp_duty_bps

    def buy_cost_bps(self) -> float:
        return self.commission_bps + self.slippage_bps

    def apply_cost(self, gross_return_pct: float) -> float:
        if not self.enable_cost_model:
            return gross_return_pct
        return gross_return_pct - self.buy_cost_bps() / 100.0

    def apply_roundtrip_cost(self, gross_return_pct: float) -> float:
        if not self.enable_cost_model:
            return gross_return_pct
        return gross_return_pct - self.total_roundtrip_bps() / 100.0
