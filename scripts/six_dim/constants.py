"""six_dim 选股 — 常量定义。

包含排序优先级、权重表、门槛阈值、分档宽度、金融股识别提示等。
"""

from __future__ import annotations

# ── 排序优先级（唯一权威顺序）──────────────────────────────────────────────────
# tiered_key / 报告列顺序 / 权重表均以此为序；调整权重不会改变排序优先级。
DIM_PRIORITY: tuple[str, ...] = (
    "logic",
    "valuation",
    "position",
    "trend",
    "holder",
    "vol_price",
)

# ── 权重（仅用于「六维同档」时的综合分兜底，按 DIM_PRIORITY 顺序递减）──────────
_DIM_WEIGHT_TABLE: dict[str, float] = {
    "logic": 0.30,
    "valuation": 0.23,
    "position": 0.15,
    "trend": 0.13,
    "holder": 0.10,
    "vol_price": 0.09,
}
# 由 DIM_PRIORITY 派生，保证权重表与优先级表顺序永远一致（缺键则导入即报错）
DIM_WEIGHTS: tuple[tuple[str, float], ...] = tuple(
    (dim, _DIM_WEIGHT_TABLE[dim]) for dim in DIM_PRIORITY
)

DIM_LABELS: dict[str, str] = {
    "logic": "逻辑",
    "valuation": "估值",
    "position": "位置",
    "trend": "趋势",
    "holder": "股东人数",
    "vol_price": "量价关系",
}

# ── 门槛常量 ───────────────────────────────────────────────────────────────────
MIN_ROE_AVG = 12.0  # 近两年 ROE 均值下限（%）
MIN_ROE_EXCL_AVG = 10.0  # 近两年扣非 ROE 均值下限（%）
MAX_DEBT_RATIO = 65.0  # 资产负债率上限（%），金融股豁免
MIN_PROFIT_YI = 1.0  # 最近年度归母净利润下限（亿元）
MIN_CFO_RATIO = 0.5  # 经营现金流 / 净利润 下限
MAX_PE = 30.0  # PE_TTM 上限
MAX_PB = 6.0  # PB 上限
MAX_PE_PCT = 60.0  # PE 五年分位上限（%）
MAX_PEG = 2.5  # PEG 上限
MIN_MARKET_CAP_YI = 50.0  # 最小总市值（亿元）
POSITION_WINDOW_DAYS = 750  # 位置分位窗口（约 3 年交易日）

# 排序口径：分档字典序（逻辑 → 估值 → 位置 → 趋势 → 股东 → 量价）
# 每一维先按 TIER_WIDTH 分档比较，档内再比下一维，六维同档才用综合分兜底。
TIER_WIDTH = 5.0

# 金融股识别（毛利率缺失 + 高负债 = 银行/保险/券商，负债率门槛豁免）
FIN_INDUSTRY_HINTS = ("银行", "保险", "证券", "信托", "多元金融")
