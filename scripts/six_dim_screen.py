"""六维选股筛选：逻辑 → 估值 → 位置 → 趋势 → 股东人数 → 量价关系。

本模块为向后兼容层，所有实现已拆分至 :mod:`scripts.six_dim` 包。
本模块重新导出 ``scripts.six_dim`` 的所有公共符号，确保现有导入
（如 ``from scripts.six_dim_screen import ...``）继续正常工作。

用法::

    uv run python scripts/six_dim_screen.py                     # 默认推荐 10 只
    uv run python scripts/six_dim_screen.py --top 20            # 推荐 20 只
    uv run python scripts/six_dim_screen.py --no-fetch          # 只用本地缓存
    uv run python scripts/six_dim_screen.py --min-cap 30        # 最小总市值 30 亿
"""

from __future__ import annotations

from scripts.six_dim import (  # noqa: F401
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
    build_feishu_card,
    fetch_holder_counts,
    fetch_spot_snapshot,
    fetch_valuation_history,
    load_fundamentals,
    load_technicals,
    main,
    score_holder,
    score_logic,
    score_position,
    score_valuation,
    score_vol_price,
    trend_gate_and_score,
    write_report,
)
from scripts.six_dim.logic import (  # noqa: F401
    _analysis_year,
    _parse_thscode,
)
from scripts.six_dim.models import (  # noqa: F401
    LogicMetrics,
    StockScore,
)
from scripts.six_dim.sorting import (  # noqa: F401
    sort_scores,
)
from scripts.six_dim.technical import (  # noqa: F401
    _compute_technicals,
)
from scripts.six_dim.universe import (  # noqa: F401
    _load_pe_pb,
    _load_universe,
    screen,
)
from scripts.six_dim.utils import (  # noqa: F401
    _clamp,
    _pct_rank,
    _recent_quarters,
)

__all__ = [
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
    "LogicMetrics",
    "StockScore",
    "sort_scores",
    "_clamp",
    "_pct_rank",
    "_recent_quarters",
    "_parse_thscode",
    "_analysis_year",
    "load_fundamentals",
    "score_logic",
    "fetch_valuation_history",
    "score_valuation",
    "_compute_technicals",
    "load_technicals",
    "score_position",
    "trend_gate_and_score",
    "score_vol_price",
    "fetch_holder_counts",
    "score_holder",
    "fetch_spot_snapshot",
    "_load_universe",
    "_load_pe_pb",
    "screen",
    "build_feishu_card",
    "write_report",
    "main",
]

if __name__ == "__main__":
    main()
