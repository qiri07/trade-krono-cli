"""
pipeline — 投研流水线核心模块。

单一编排入口：QuantPipeline（TA + Kronos 并行）+ PipelineFactory（组件工厂）。
底层函数从子模块直接导入：
  - from trade_krono_cli.pipeline.merge import merge_results, filter_pool, default_scorer
  - from trade_krono_cli.pipeline.reporter import save_json_report, save_html_report, ...
"""

from __future__ import annotations

from trade_krono_cli.pipeline.orchestrator import PipelineFactory, QuantPipeline

__all__ = ["QuantPipeline", "PipelineFactory"]
