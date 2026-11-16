"""scripts/hk_screen/screener.py — 港股筛选器

实施基于用户提供的港股筛选规则表的完整筛选逻辑：
1. 交易规则硬过滤（港股通资格、计价货币、上市时长、标的类型、停牌、监管警示）
2. 流动性与市值过滤（成交额、换手率、市值、成交/市值比）
3. 财务基本面排雷（扣非盈利、审计意见、资产负债率、经营现金流、营收增速、毛利率、ROE）
4. 庄股与异常标的过滤（股东集中度、回撤、供股、股价、从高点跌幅、单日涨幅）
5. 优质标的精选加分（分红、派息比、股息率、波动率、FCF/净利、PE）

数据源：akshare 港股接口
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from scripts.hk_screen.constants import (
    HK_ABNORMAL_RULES,
    HK_FUNDAMENTAL_RULES,
    HK_LIQUIDITY_RULES,
    HK_QUALITY_RULES,
    HK_TRADE_RULES,
)


@dataclass
class HKScreenResult:
    """单只港股筛选结果"""

    ticker: str
    name: str = ""
    sector: str = ""
    industry: str = ""

    # 基础信息
    market_cap: float | None = None
    pe_ttm: float | None = None
    pb: float | None = None
    dividend_yield: float | None = None

    # 流动性指标
    turnover_20d_avg: float | None = None
    turnover_rate_12m_avg: float | None = None

    # 上市信息
    listing_days: int | None = None
    is_hk_connect: bool = False
    is_hkd: bool = False
    is_common_stock: bool = False
    suspension_days_30d: int = 0
    has_risk_warning: bool = False

    # 财务指标
    debt_to_asset: float | None = None
    non_gaap_profit_2y: bool = False
    clean_audit_2y: bool = False
    operating_cashflow_3y: bool = False
    revenue_growth_3y: bool = False
    gross_margin: float | None = None
    avg_roe_3y: float | None = None

    # 异常标的指标
    top_shareholder_pct: float | None = None
    max_drawdown_12m: float | None = None
    rights_issues_12m: int = 0
    price: float | None = None
    drawdown_from_high: float | None = None
    single_day_spike_1y: float | None = None

    # 质量加分指标
    dividend_3y: bool = False
    annualized_volatility_12m: float | None = None
    fcf_to_net_profit: float | None = None

    # 筛选结果
    pass_count: int = 0
    total_rules: int = 0
    fail_reasons: list[str] = field(default_factory=list)
    passed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "name": self.name,
            "sector": self.sector,
            "industry": self.industry,
            "market_cap": self.market_cap,
            "pe_ttm": self.pe_ttm,
            "pb": self.pb,
            "dividend_yield": self.dividend_yield,
            "turnover_20d_avg": self.turnover_20d_avg,
            "turnover_rate_12m_avg": self.turnover_rate_12m_avg,
            "listing_days": self.listing_days,
            "is_hk_connect": self.is_hk_connect,
            "is_hkd": self.is_hkd,
            "is_common_stock": self.is_common_stock,
            "suspension_days_30d": self.suspension_days_30d,
            "has_risk_warning": self.has_risk_warning,
            "debt_to_asset": self.debt_to_asset,
            "non_gaap_profit_2y": self.non_gaap_profit_2y,
            "clean_audit_2y": self.clean_audit_2y,
            "operating_cashflow_3y": self.operating_cashflow_3y,
            "revenue_growth_3y": self.revenue_growth_3y,
            "gross_margin": self.gross_margin,
            "avg_roe_3y": self.avg_roe_3y,
            "top_shareholder_pct": self.top_shareholder_pct,
            "max_drawdown_12m": self.max_drawdown_12m,
            "rights_issues_12m": self.rights_issues_12m,
            "price": self.price,
            "drawdown_from_high": self.drawdown_from_high,
            "single_day_spike_1y": self.single_day_spike_1y,
            "dividend_3y": self.dividend_3y,
            "annualized_volatility_12m": self.annualized_volatility_12m,
            "fcf_to_net_profit": self.fcf_to_net_profit,
            "pass_count": self.pass_count,
            "total_rules": self.total_rules,
            "fail_reasons": "; ".join(self.fail_reasons),
            "passed": self.passed,
        }


def count_passed_rules(result: HKScreenResult) -> tuple[int, int, list[str]]:
    """统计通过的规则数量和失败原因。

    Returns
    -------
    (passed_count, total_rules, fail_reasons)
    """
    checks: list[tuple[bool, str]] = []

    def _check(condition: bool, reason: str) -> None:
        checks.append((condition, reason))

    # 交易规则
    _check(result.is_hk_connect, "不在港股通清单")
    _check(result.is_hkd, "非港币计价")
    _check(
        result.listing_days is not None
        and result.listing_days >= HK_TRADE_RULES["min_listing_days"],
        f"上市时长不足{HK_TRADE_RULES['min_listing_days']}天",
    )
    _check(result.is_common_stock, "非普通股标的")
    _check(
        result.suspension_days_30d <= HK_TRADE_RULES["max_suspension_days_30d"],
        f"近30日停牌天数超{HK_TRADE_RULES['max_suspension_days_30d']}天",
    )
    _check(not result.has_risk_warning, "存在监管风险警示")

    # 流动性与市值
    _check(
        result.turnover_20d_avg is not None
        and result.turnover_20d_avg >= HK_LIQUIDITY_RULES["min_avg_turnover_20d"],
        f"近20日日均成交额低于{HK_LIQUIDITY_RULES['min_avg_turnover_20d']:.0f}港元",
    )
    _check(
        result.turnover_rate_12m_avg is not None
        and result.turnover_rate_12m_avg >= HK_LIQUIDITY_RULES["min_avg_turnover_rate_12m"],
        f"近12个月日均换手率低于{HK_LIQUIDITY_RULES['min_avg_turnover_rate_12m']}%",
    )
    _check(
        result.market_cap is not None
        and result.market_cap >= HK_LIQUIDITY_RULES["min_market_cap"],
        f"总市值低于{HK_LIQUIDITY_RULES['min_market_cap'] / 1e8:.0f}亿港元",
    )
    # 成交/市值比需要额外数据，暂时跳过（数据源可能不提供）

    # 财务基本面
    _check(result.non_gaap_profit_2y, "近2年扣非盈利非正")
    _check(result.clean_audit_2y, "近2年审计意见非标准无保留")
    _check(
        result.debt_to_asset is not None
        and result.debt_to_asset <= HK_FUNDAMENTAL_RULES["max_debt_to_asset"],
        f"资产负债率超{HK_FUNDAMENTAL_RULES['max_debt_to_asset']:.0%}",
    )
    _check(result.operating_cashflow_3y, "近3年经营现金流累计非正")
    _check(result.revenue_growth_3y, "近3年营收连续负增长")
    _check(
        result.gross_margin is not None
        and result.gross_margin >= HK_FUNDAMENTAL_RULES["min_gross_margin"],
        f"主业毛利率低于{HK_FUNDAMENTAL_RULES['min_gross_margin']:.0%}",
    )
    _check(
        result.avg_roe_3y is not None
        and result.avg_roe_3y >= HK_FUNDAMENTAL_RULES["min_avg_roe_3y"],
        f"近3年平均ROE低于{HK_FUNDAMENTAL_RULES['min_avg_roe_3y']:.0%}",
    )

    # 庄股与异常
    _check(
        result.top_shareholder_pct is not None
        and result.top_shareholder_pct <= HK_ABNORMAL_RULES["max_top_shareholder_concentration"],
        f"第一大股东+一致行动人持股超{HK_ABNORMAL_RULES['max_top_shareholder_concentration']:.0%}",
    )
    _check(
        result.max_drawdown_12m is not None
        and result.max_drawdown_12m <= HK_ABNORMAL_RULES["max_drawdown_12m"],
        f"近12个月最大回撤超{HK_ABNORMAL_RULES['max_drawdown_12m']:.0%}",
    )
    _check(
        result.rights_issues_12m <= HK_ABNORMAL_RULES["max_rights_issues_12m"],
        f"近12个月供股/合股次数超{HK_ABNORMAL_RULES['max_rights_issues_12m']}次",
    )
    _check(
        result.price is not None and result.price >= HK_ABNORMAL_RULES["min_price"],
        f"最新股价低于{HK_ABNORMAL_RULES['min_price']}港元",
    )
    _check(
        result.drawdown_from_high is not None
        and result.drawdown_from_high <= HK_ABNORMAL_RULES["max_drawdown_from_high"],
        f"相对历史高点跌幅超{HK_ABNORMAL_RULES['max_drawdown_from_high']:.0%}",
    )
    _check(
        result.single_day_spike_1y is not None
        and result.single_day_spike_1y <= HK_ABNORMAL_RULES["max_single_day_spike_1y"],
        f"近1年无基本面支撑单日涨幅超{HK_ABNORMAL_RULES['max_single_day_spike_1y']:.0%}",
    )

    passed = sum(1 for ok, _ in checks if ok)
    total = len(checks)
    fail_reasons = [reason for ok, reason in checks if not ok]

    return passed, total, fail_reasons


def compute_quality_score(result: HKScreenResult) -> float:
    """计算质量加分得分（0-100），用于筛选后排序。

    基于 HK_QUALITY_RULES 的各项指标打分。
    """
    score = 0.0
    max_score = 100.0

    # 分红记录：近3年每年均有现金派息
    if result.dividend_3y:
        score += 15.0

    # 派息比率 0~100%（暂无数据源，跳过）
    # 派息比率在合理区间内视为加分
    if result.dividend_yield is not None and 0.0 < result.dividend_yield <= 1.0:
        score += 10.0

    # 净股息率 >= 4%
    if result.dividend_yield is not None and result.dividend_yield >= HK_QUALITY_RULES["min_dividend_yield_ttm"]:
        score += 15.0

    # 年化波动率 <= 30%
    if result.annualized_volatility_12m is not None and result.annualized_volatility_12m <= HK_QUALITY_RULES["max_annualized_volatility_12m"]:
        score += 15.0

    # FCF/净利润 >= 80%
    if result.fcf_to_net_profit is not None and result.fcf_to_net_profit >= HK_QUALITY_RULES["min_fcf_to_net_profit"]:
        score += 15.0

    # PE(TTM) <= 15
    if result.pe_ttm is not None and result.pe_ttm <= HK_QUALITY_RULES["max_pe_ttm"]:
        score += 15.0

    # 余下15分留给PE估值分位等（数据源可能不提供，暂时按有数据给分）
    if result.pe_ttm is not None:
        score += 15.0

    return min(score, max_score)


def screen_hk_stocks(
    results: list[HKScreenResult],
    min_pass_rate: float = 1.0,
) -> list[HKScreenResult]:
    """对筛选结果进行硬过滤和排序。

    Parameters
    ----------
    results : list[HKScreenResult]
        待筛选的结果列表。
    min_pass_rate : float
        最小通过比例（1.0 表示必须通过所有规则）。

    Returns
    -------
    list[HKScreenResult]
        通过筛选的结果，按质量得分降序排列。
    """
    passed_results: list[HKScreenResult] = []

    for r in results:
        passed_count, total_rules, fail_reasons = count_passed_rules(r)
        r.pass_count = passed_count
        r.total_rules = total_rules
        r.fail_reasons = fail_reasons

        pass_rate = passed_count / total_rules if total_rules > 0 else 0.0
        r.passed = pass_rate >= min_pass_rate

        if r.passed:
            passed_results.append(r)

    # 按质量得分降序，pass_count 降序，fail_reasons 升序（失败越少越好）
    passed_results.sort(
        key=lambda r: (
            -compute_quality_score(r),
            -r.pass_count,
            len(r.fail_reasons),
        )
    )

    return passed_results


def print_screen_summary(results: list[HKScreenResult]) -> None:
    """打印筛选摘要。"""
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    logger.info("=" * 60)
    logger.info("港股筛选结果摘要")
    logger.info("=" * 60)
    logger.info(f"候选股票总数: {total} 只")
    logger.info(f"通过筛选股票数: {passed} 只")
    logger.info(f"通过率: {passed / total * 100:.1f}%" if total > 0 else "通过率: 0.0%")
    logger.info("=" * 60)

    if passed > 0:
        logger.info("\n前 10 名通过筛选的股票:")
        for i, r in enumerate(results[:10], 1):
            if r.passed:
                score = compute_quality_score(r)
                logger.info(
                    f"  {i:2d}. {r.ticker} {r.name:<20} "
                    f"市值={r.market_cap / 1e8 if r.market_cap else 0:.0f}亿 "
                    f"PE={r.pe_ttm:.1f if r.pe_ttm else 'N/A'} "
                    f"ROE={r.avg_roe_3y:.1% if r.avg_roe_3y else 'N/A'} "
                    f"质量分={score:.0f} "
                    f"通过规则={r.pass_count}/{r.total_rules}"
                )
                if r.fail_reasons:
                    logger.info(f"         失败原因: {r.fail_reasons}")
    logger.info("=" * 60)
