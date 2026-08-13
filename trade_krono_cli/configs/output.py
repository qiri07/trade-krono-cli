"""输出路径配置。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=False)
class OutputConfig:
    """报告输出路径配置。"""

    output_dir: Path = Path("outputs")
    json_path: str = "outputs/results.json"
    html_path: str = "outputs/report.html"

    def merge(self, **overrides) -> "OutputConfig":
        current = {k: getattr(self, k) for k in self.__dataclass_fields__}
        current.update(overrides)
        return OutputConfig(**current)
