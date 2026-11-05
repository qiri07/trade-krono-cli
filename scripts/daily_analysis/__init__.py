#!/usr/bin/env python3
"""白名单股票综合分析 + AI 核实 + 飞书推送（包入口）。

基于缓存 K 线数据进行基础技术分析（MA/量能/涨跌/估值），
支持在 TA/Kronos 外部模块不可用时也能正常输出分析结果。

向后兼容：保留 scripts.daily_analysis 单文件时代的导入路径，
所有公开 API 通过此模块导出，原有 import 语句无需修改。

模块结构：
- names.py     : 股票名称解析（预加载 + 逐只兜底）
- whitelist.py : 静态/动态白名单合并、ST/北交所过滤、缓存路径
- llm.py       : LLM AI 核实（统一配置 + 限流/退避）
- kline.py     : K 线缓存读取与外部补拉
- core.py      : 技术分析核心（analyze_stock / _run_analysis / 估值）
- card.py      : 飞书单股卡片文本
- runner.py    : 主流程 run_analysis / main（CLI 入口）

用法：
  uv run python -m scripts.daily_analysis
  uv run python -m scripts.daily_analysis --date 2026-09-15
  uv run python -m scripts.daily_analysis --tickers "000001,002027"
"""

from __future__ import annotations

from scripts.daily_analysis.card import _build_stock_card
from scripts.daily_analysis.core import (
    _check_regime_guard,
    _fetch_pe_data,
    _run_analysis,
    analyze_stock,
)
from scripts.daily_analysis.kline import _fetch_and_save_kline, get_kline
from scripts.daily_analysis.llm import _ai_available, _extract_json, _get_llm_client, ai_verify
from scripts.daily_analysis.names import _get_stock_name, _preload_stock_names
from scripts.daily_analysis.runner import main, run_analysis
from scripts.daily_analysis.whitelist import (
    _filter_bse_st,
    _get_cache_db,
    _get_dynamic_whitelist,
    _get_merged_whitelist,
    _get_whitelist,
)
from trade_krono_cli.cli_commands._core_helpers import _load_env

__all__ = [
    "_ai_available",
    "_build_stock_card",
    "_check_regime_guard",
    "_extract_json",
    "_fetch_and_save_kline",
    "_fetch_pe_data",
    "_filter_bse_st",
    "_get_cache_db",
    "_get_dynamic_whitelist",
    "_get_llm_client",
    "_get_merged_whitelist",
    "_get_stock_name",
    "_get_whitelist",
    "_load_env",
    "_preload_stock_names",
    "_run_analysis",
    "ai_verify",
    "analyze_stock",
    "get_kline",
    "main",
    "run_analysis",
]
