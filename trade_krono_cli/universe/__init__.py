"""
Universe Engine — A 股市场范围发现与分层过滤。

职责：
  · 从全市场 A 股中，通过多阶段过滤产生可进入 TA/Kronos 的股票池
  · 各阶段独立、可测试、可组合
  · 支持 akshare 等数据源提供者

架构：
  UniverseProvider   → 获取全市场 tickers（如 akshare.stock_zh_a_spot_em）
  Stage[Static|Fundamental|Factor] → 各层过滤
  UniverseEngine     → 编排完整流程，输出 list[str]

用法：
    engine = UniverseEngine.from_config(filter_config)
    tickers = engine.run(eval_date="2026-08-13")
    # → ["sh.600519", "sz.000858", ...]
"""
from __future__ import annotations

from trade_krono_cli.universe.engine import UniverseEngine
from trade_krono_cli.universe.provider import UniverseProvider

__all__ = [
    "UniverseEngine",
    "UniverseProvider",
]
