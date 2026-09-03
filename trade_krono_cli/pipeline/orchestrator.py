"""orchestrator — 调度主循环（向后兼容入口）。

QuantPipeline 的核心实现已拆分为 pipeline/pipeline_core.py，
PipelineFactory 拆分为 pipeline/factory.py。
本模块为薄包装，保持向后兼容的导入路径。
"""

from __future__ import annotations

from trade_krono_cli.pipeline.factory import (
    PipelineFactory,
    _collect_futures,
)
from trade_krono_cli.pipeline.pipeline_core import QuantPipeline

__all__ = ["PipelineFactory", "QuantPipeline", "_collect_futures"]
