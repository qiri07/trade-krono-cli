"""Kronos 预测配置。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=False)
class KronosConfig:
    """Kronos 模型预测参数。"""

    sample_count: int = 5
    pred_len: int = 30
    lookback: int = 400
    model_name: str = "kronos-base"
    device: str = "cpu"
    T: float = 1.0
    top_p: float = 0.9
    use_cache: bool = True

    def merge(self, **overrides) -> KronosConfig:
        current = {k: getattr(self, k) for k in self.__dataclass_fields__}
        current.update({k: v for k, v in overrides.items() if v is not None})
        return KronosConfig(**current)

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.sample_count < 1:
            errors.append(f"kronos.sample_count={self.sample_count} 必须 >= 1")
        if self.pred_len < 1:
            errors.append(f"kronos.pred_len={self.pred_len} 必须 >= 1")
        if self.lookback < 10:
            errors.append(f"kronos.lookback={self.lookback} 必须 >= 10")
        if self.T <= 0:
            errors.append(f"kronos.T={self.T} 必须 > 0")
        if not (0 < self.top_p <= 1.0):
            errors.append(f"kronos.top_p={self.top_p} 必须在 (0, 1.0] 范围内")
        if not self.model_name or not self.model_name.strip():
            errors.append("kronos.model_name 不能为空")
        return errors
