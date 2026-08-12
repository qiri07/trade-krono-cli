"""
流水线编排 — 薄代理层，委托给 pipeline/orchestrator.py。

保留向后兼容：from trade_krono_cli.pipeline import QuantPipeline 仍然有效。
"""
from __future__ import annotations

from trade_krono_cli.pipeline.orchestrator import QuantPipeline

__all__ = ["QuantPipeline"]
