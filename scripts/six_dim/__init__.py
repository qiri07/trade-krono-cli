"""six_dim 包 — 六维选股筛选：逻辑 → 估值 → 位置 → 趋势 → 股东人数 → 量价关系。

本包由原 ``scripts/six_dim_screen.py``（1365 行）拆分而成，按功能模块组织：

- :mod:`scripts.six_dim.constants`   — 排序优先级、权重、门槛常量
- :mod:`scripts.six_dim.models`      — LogicMetrics / StockScore 数据模型
- :mod:`scripts.six_dim.sorting`     — 分档字典序排序 (sort_scores)
- :mod:`scripts.six_dim.utils`       — 工具函数 (_clamp / _pct_rank / _recent_quarters)
- :mod:`scripts.six_dim.logic`       — 逻辑维度（基本面加载 + 打分）
- :mod:`scripts.six_dim.valuation`   — 估值维度（PE/PB 五年分位）
- :mod:`scripts.six_dim.technical`   — 技术面（位置 / 趋势 / 量价）
- :mod:`scripts.six_dim.holder`      — 股东人数维度
- :mod:`scripts.six_dim.market_cap`  — 总市值快照
- :mod:`scripts.six_dim.universe`    — 主流程 (screen / _load_universe)
- :mod:`scripts.six_dim.cards`       — 飞书汇总卡片 (build_feishu_card)
- :mod:`scripts.six_dim.report`      — 报告写入 (write_report)
- :mod:`scripts.six_dim.cli`         — CLI 入口 (main)

向后兼容：原 ``scripts.six_dim_screen`` 模块作为兼容层，重新导出本包的所有公共符号。
"""

from __future__ import annotations

from scripts.six_dim.cards import build_feishu_card
from scripts.six_dim.cli import main
from scripts.six_dim.constants import (
    DIM_LABELS,
    DIM_PRIORITY,
    DIM_WEIGHTS,
    FIN_INDUSTRY_HINTS,
    MAX_DEBT_RATIO,
    MAX_PB,
    MAX_PE,
    MAX_PE_PCT,
    MAX_PEG,
    MIN_CFO_RATIO,
    MIN_MARKET_CAP_YI,
    MIN_PROFIT_YI,
    MIN_ROE_AVG,
    MIN_ROE_EXCL_AVG,
    POSITION_WINDOW_DAYS,
    TIER_WIDTH,
)
from scripts.six_dim.holder import fetch_holder_counts, score_holder
from scripts.six_dim.logic import (
    _analysis_year,
    _parse_thscode,
    load_fundamentals,
    score_logic,
)
from scripts.six_dim.market_cap import fetch_spot_snapshot
from scripts.six_dim.models import LogicMetrics, StockScore
from scripts.six_dim.report import write_report
from scripts.six_dim.sorting import sort_scores
from scripts.six_dim.technical import (
    _compute_technicals,
    load_technicals,
    score_position,
    score_vol_price,
    trend_gate_and_score,
)
from scripts.six_dim.universe import _load_pe_pb, _load_universe, screen
from scripts.six_dim.utils import _clamp, _pct_rank, _recent_quarters
from scripts.six_dim.valuation import fetch_valuation_history, score_valuation

__all__ = [
    # constants
    "DIM_PRIORITY",
    "DIM_WEIGHTS",
    "DIM_LABELS",
    "TIER_WIDTH",
    "FIN_INDUSTRY_HINTS",
    "MIN_ROE_AVG",
    "MIN_ROE_EXCL_AVG",
    "MAX_DEBT_RATIO",
    "MIN_PROFIT_YI",
    "MIN_CFO_RATIO",
    "MAX_PE",
    "MAX_PB",
    "MAX_PE_PCT",
    "MAX_PEG",
    "MIN_MARKET_CAP_YI",
    "POSITION_WINDOW_DAYS",
    # models
    "LogicMetrics",
    "StockScore",
    # sorting
    "sort_scores",
    # utils
    "_clamp",
    "_pct_rank",
    "_recent_quarters",
    # logic
    "_parse_thscode",
    "_analysis_year",
    "load_fundamentals",
    "score_logic",
    # valuation
    "fetch_valuation_history",
    "score_valuation",
    # technical
    "_compute_technicals",
    "load_technicals",
    "score_position",
    "trend_gate_and_score",
    "score_vol_price",
    # holder
    "fetch_holder_counts",
    "score_holder",
    # market_cap
    "fetch_spot_snapshot",
    # universe
    "_load_universe",
    "_load_pe_pb",
    "screen",
    # cards
    "build_feishu_card",
    # report
    "write_report",
    # cli
    "main",
]
