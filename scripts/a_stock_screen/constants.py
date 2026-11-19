"""scripts/a_stock_screen/constants.py — A股筛选规则常量

基于 scripts/hk_screen/constants.py 的港股筛选规则表，适配 A股 市场实施。
所有金额单位为人民币元（除百分比/比率外）。
"""

from __future__ import annotations

# ════════════════════════════════════════════════════════════════════════════════
# 交易规则硬过滤指标
# ════════════════════════════════════════════════════════════════════════════════

ASTOCK_TRADE_RULES = {
    # 剔除 ST / *ST / 退市股票
    "exclude_st": True,
    # 剔除北交所股票（bj. 前缀或 920/43/83/87 开头裸代码）
    "exclude_bse": True,
    # 剔除 ETF（ETF 以纯技术面参与各筛选线，不受本基本面/流动性硬过滤）
    "exclude_etf": True,
    # 上市时长：>= 30 个自然日（剔除过于次新的股票）
    "min_listing_days": 30,
    # 近 30 个交易日累计停牌天数 <= 5 天（通过成交量为 0 判断停牌）
    "max_suspension_days_30d": 5,
    # 最新股价 >= 1 元（避免面值退市风险股）
    "min_price": 1.0,
}

# ════════════════════════════════════════════════════════════════════════════════
# 流动性与市值过滤指标
# ════════════════════════════════════════════════════════════════════════════════

ASTOCK_LIQUIDITY_RULES = {
    # 近 20 个交易日日均成交额 >= 50,000,000 元（5000 万元人民币）
    "min_avg_turnover_20d": 50_000_000.0,  # 元
    # 近 12 个月日均换手率 >= 0.1%
    "min_avg_turnover_rate_12m": 0.1,  # %
    # 最新总市值 >= 5,000,000,000 元（50 亿元人民币，与 _utils.MIN_MARKET_CAP_YI 一致）
    "min_market_cap": 5_000_000_000.0,  # 元
    # 近 12 个月累计成交额/总市值 >= 50%
    "min_turnover_to_market_cap_ratio_12m": 0.50,  # 50%
}

# ════════════════════════════════════════════════════════════════════════════════
# 财务基本面排雷指标（可选，作为软过滤/加分参考）
# ════════════════════════════════════════════════════════════════════════════════

ASTOCK_FUNDAMENTAL_RULES = {
    # 资产负债率 <= 80%
    "max_debt_to_asset": 0.80,
    # 经营活动现金流净额：近 3 年累计值 > 0
    "require_operating_cashflow_positive_3y": True,
    # 主业毛利率 >= 10%
    "min_gross_margin": 0.10,
    # 近 3 年平均 ROE >= 15%
    "min_avg_roe_3y": 0.15,
}

# 所有硬过滤规则的合并配置（便于遍历）
ALL_HARD_FILTER_RULES: dict[str, dict] = {
    "trade": ASTOCK_TRADE_RULES,
    "liquidity": ASTOCK_LIQUIDITY_RULES,
    "fundamental": ASTOCK_FUNDAMENTAL_RULES,
}

# 输出列名常量
OUTPUT_COLUMNS = [
    "code",
    "name",
    "close",
    "market_cap",
    "pe_ttm",
    "pb",
    "turnover_20d_avg",
    "turnover_rate_12m_avg",
    "listing_days",
    "suspension_days_30d",
    "is_st",
    "is_etf",
    "is_bse",
    "debt_to_asset",
    "gross_margin",
    "avg_roe_3y",
    "pass_count",
    "total_rules",
    "fail_reasons",
    "passed",
]
