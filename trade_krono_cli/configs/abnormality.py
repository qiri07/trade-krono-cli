"""异常股票处理配置。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=False)
class AbnormalityConfig:
    """异常股票（ST/次新/停牌）处理参数。"""

    skip_new_stock: bool = True
    new_stock_min_days: int = 60
    kline_min_completeness: float = 0.85
    abnormality_risk_boost_enabled: bool = True

    def merge(self, **overrides) -> "AbnormalityConfig":
        current = {k: getattr(self, k) for k in self.__dataclass_fields__}
        current.update(overrides)
        return AbnormalityConfig(**current)

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.new_stock_min_days < 5:
            errors.append(
                f"abnormality.new_stock_min_days={self.new_stock_min_days} 必须 >= 5"
            )
        kc = self.kline_min_completeness
        if not (0 < kc <= 1.0):
            errors.append(
                f"abnormality.kline_min_completeness={kc} 必须在 (0, 1.0] 范围内"
            )
        return errors
