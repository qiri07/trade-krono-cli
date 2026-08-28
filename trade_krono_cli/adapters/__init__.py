"""
适配器层 — 隔离外部依赖（cli_anything / TradingAgents-astock / Kronos）。

对外暴露适配器实现类，业务代码（runner / pipeline）只依赖此处导出的接口。
"""

from __future__ import annotations

from trade_krono_cli.adapters.base import KronosAdapter, TradingAgentsAdapter
from trade_krono_cli.adapters.kronos import KronosAdapterImpl
from trade_krono_cli.adapters.tradingagents import TradingAgentsAdapterImpl

__all__ = (
    "TradingAgentsAdapter",
    "KronosAdapter",
    "TradingAgentsAdapterImpl",
    "KronosAdapterImpl",
)
