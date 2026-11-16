"""scripts/hk_screen/constants.py — 港股筛选规则常量

基于用户提供的港股筛选规则表实施。
"""

from __future__ import annotations

# ════════════════════════════════════════════════════════════════════════════════
# 交易规则硬过滤指标
# ════════════════════════════════════════════════════════════════════════════════

HK_TRADE_RULES = {
    # 港股通合资格状态：必须在交易所最新公布的港股通标的清单内
    "require_hk_connect": True,
    # 计价货币：仅保留港币计价标的
    "require_hkd_currency": True,
    # 上市时长：>= 180 个自然日
    "min_listing_days": 180,
    # 交易标的类型：仅保留普通股，剔除窝轮、牛熊证、衍生品等
    "exclude_derivatives": True,
    # 停牌状态：近 30 个交易日累计停牌天数 <= 5 天
    "max_suspension_days_30d": 5,
    # 监管警示状态：无港交所/证监会公开风险警示、立案调查记录
    "require_no_risk_warning": True,
}

# ════════════════════════════════════════════════════════════════════════════════
# 流动性与市值过滤指标
# ════════════════════════════════════════════════════════════════════════════════

HK_LIQUIDITY_RULES = {
    # 近 20 个交易日日均成交额 >= 5000 万港元
    "min_avg_turnover_20d": 50_000_000.0,  # 港元
    # 近 12 个月日均换手率 >= 0.1%
    "min_avg_turnover_rate_12m": 0.1,  # %
    # 最新总市值 >= 10 亿港元
    "min_market_cap": 1_000_000_000.0,  # 港元
    # 近 12 个月累计成交总额/总市值 >= 50%
    "min_turnover_to_market_cap_ratio_12m": 0.50,  # 50%
}

# ════════════════════════════════════════════════════════════════════════════════
# 财务基本面排雷指标
# ════════════════════════════════════════════════════════════════════════════════

HK_FUNDAMENTAL_RULES = {
    # 连续扣非盈利时长：近 2 年扣非净利润均为正
    "require_non_gaap_profit_2y": True,
    # 审计意见类型：近 2 年财报均为标准无保留意见
    "require_clean_audit_2y": True,
    # 资产负债率 <= 80%
    "max_debt_to_asset": 0.80,
    # 经营活动现金流净额：近 3 年累计值 > 0
    "require_operating_cashflow_positive_3y": True,
    # 营收同比增速：近 3 年无连续负增长
    "require_no_consecutive_revenue_decline_3y": True,
    # 主业毛利率 >= 10%
    "min_gross_margin": 0.10,
    # 近 3 年平均 ROE >= 15%
    "min_avg_roe_3y": 0.15,
}

# ════════════════════════════════════════════════════════════════════════════════
# 庄股与异常标的过滤指标
# ════════════════════════════════════════════════════════════════════════════════

HK_ABNORMAL_RULES = {
    # 第一大股东+一致行动人持股比例 <= 90%
    "max_top_shareholder_concentration": 0.90,
    # 近 12 个月股价最大回撤 <= 50%
    "max_drawdown_12m": 0.50,
    # 近 12 个月累计供股/合股次数 <= 1 次
    "max_rights_issues_12m": 1,
    # 最新股价 >= 1 港元
    "min_price": 1.0,  # 港元
    # 复权价相对历史高点跌幅 <= 80%
    "max_drawdown_from_high": 0.80,
    # 近 1 年无基本面支撑单日涨幅 <= 30%
    "max_single_day_spike_1y": 0.30,
}

# ════════════════════════════════════════════════════════════════════════════════
# 优质标的精选加分指标（用于排序，不作为硬过滤）
# ════════════════════════════════════════════════════════════════════════════════

HK_QUALITY_RULES = {
    # 连续现金分红时长：近 3 年每年均有现金派息
    "require_dividend_3y": True,
    # 派息比率：0 ~ 100% 区间
    "payout_ratio_min": 0.0,
    "payout_ratio_max": 1.0,
    # 净股息率(TTM) >= 4%
    "min_dividend_yield_ttm": 0.04,
    # 近 12 个月股价年化波动率 <= 30%
    "max_annualized_volatility_12m": 0.30,
    # 自由现金流/净利润 >= 80%
    "min_fcf_to_net_profit": 0.80,
    # PE(TTM) <= 15 倍
    "max_pe_ttm": 15.0,
}

# 所有硬过滤规则的合并配置（便于遍历）
ALL_HARD_FILTER_RULES: dict[str, dict] = {
    "trade": HK_TRADE_RULES,
    "liquidity": HK_LIQUIDITY_RULES,
    "fundamental": HK_FUNDAMENTAL_RULES,
    "abnormal": HK_ABNORMAL_RULES,
}

# 输出列名常量
OUTPUT_COLUMNS = [
    "ticker",
    "name",
    "sector",
    "industry",
    "market_cap",
    "pe_ttm",
    "pb",
    "dividend_yield",
    "turnover_20d_avg",
    "turnover_rate_12m_avg",
    "listing_days",
    "is_hk_connect",
    "is_hkd",
    "is_common_stock",
    "suspension_days_30d",
    "has_risk_warning",
    "debt_to_asset",
    "non_gaap_profit_2y",
    "clean_audit_2y",
    "operating_cashflow_3y",
    "revenue_growth_3y",
    "gross_margin",
    "avg_roe_3y",
    "top_shareholder_pct",
    "max_drawdown_12m",
    "rights_issues_12m",
    "price",
    "drawdown_from_high",
    "single_day_spike_1y",
    "dividend_3y",
    "annualized_volatility_12m",
    "fcf_to_net_profit",
    "pass_count",
    "fail_reasons",
]
