"""scripts/hk_screen/screener.py — 港股筛选器

实施港股筛选规则表的完整筛选逻辑：
1. 交易规则硬过滤（港股通资格、计价货币、上市时长、标的类型、停牌、监管警示）
2. 流动性与市值过滤（成交额、换手率、市值）
3. 财务基本面排雷（盈利、审计、资产负债率、经营现金流、营收增速、毛利率、ROE）
4. 庄股与异常标的过滤（股东集中度、回撤、供股、股价、从高点跌幅、单日涨幅）
5. 优质标的精选加分（分红、派息比、股息率、波动率、FCF/净利、PE）

三态语义（重要）
----------------
指标取值有三种状态：``True``（满足）/ ``False``（明确不满足）/ ``None``（未知）。
- ``None`` **不计为失败**，但计入 ``unknown``，并由 ``coverage`` 反映数据完整度；
- 通过率 = 通过数 / 已评估数，``coverage`` = 已评估数 / 全部规则数；
- 只有 ``pass_rate >= min_pass_rate`` 且 ``coverage >= min_coverage`` 才判为通过，
  避免「数据越缺失越容易通过」的 fail-open 行为。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd
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
    """单只港股筛选结果（``None`` 表示数据未知）。"""

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
    is_hk_connect: bool | None = None
    is_hkd: bool | None = None
    is_common_stock: bool | None = None
    suspension_days_30d: int | None = None
    has_risk_warning: bool | None = None

    # 财务指标
    debt_to_asset: float | None = None
    non_gaap_profit_2y: bool | None = None
    clean_audit_2y: bool | None = None
    operating_cashflow_3y: bool | None = None
    revenue_growth_3y: bool | None = None
    gross_margin: float | None = None
    avg_roe_3y: float | None = None

    # 异常标的指标
    top_shareholder_pct: float | None = None
    max_drawdown_12m: float | None = None
    rights_issues_12m: int | None = None
    price: float | None = None
    drawdown_from_high: float | None = None
    single_day_spike_1y: float | None = None

    # 质量加分指标
    dividend_3y: bool | None = None
    payout_ratio: float | None = None
    annualized_volatility_12m: float | None = None
    fcf_to_net_profit: float | None = None

    # 筛选结果
    pass_count: int = 0
    total_rules: int = 0
    unknown_count: int = 0
    coverage: float = 0.0
    fail_reasons: list[str] = field(default_factory=list)
    passed: bool = False

    def to_dict(self) -> dict[str, Any]:
        """转为可序列化字典（含筛选结论）。"""
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
            "payout_ratio": self.payout_ratio,
            "annualized_volatility_12m": self.annualized_volatility_12m,
            "fcf_to_net_profit": self.fcf_to_net_profit,
            "pass_count": self.pass_count,
            "total_rules": self.total_rules,
            "unknown_count": self.unknown_count,
            "coverage": round(self.coverage, 4),
            "fail_reasons": "; ".join(self.fail_reasons),
            "passed": self.passed,
        }


@dataclass
class RuleVerdict:
    """单只股票的规则判定结果。"""

    passed: int = 0
    evaluated: int = 0
    unknown: int = 0
    fail_reasons: list[str] = field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        """已评估规则中的通过比例。"""
        return self.passed / self.evaluated if self.evaluated > 0 else 0.0

    @property
    def coverage(self) -> float:
        """已评估规则占全部规则的比例（数据完整度）。"""
        total = self.evaluated + self.unknown
        return self.evaluated / total if total > 0 else 0.0


def count_passed_rules(
    result: HKScreenResult,
    require_hk_connect: bool = False,
) -> RuleVerdict:
    """统计通过/失败/未知的规则数量。

    Args:
        result: 待检查的股票结果。
        require_hk_connect: 是否把港股通资格作为硬性规则。为 ``False`` 时
            该规则**完全不计入**（既不通过也不未知），避免白送一条「通过」。

    Returns:
        :class:`RuleVerdict`，含通过数、已评估数、未知数与失败原因。
    """
    verdict = RuleVerdict()

    def _check(condition: bool | None, reason: str) -> None:
        """condition: True=通过, False=失败, None=数据缺失未知。"""
        if condition is None:
            verdict.unknown += 1
            return
        verdict.evaluated += 1
        if condition:
            verdict.passed += 1
        else:
            verdict.fail_reasons.append(reason)

    def _check_value(
        value: float | int | None,
        threshold: float | int,
        op: str,
        reason: str,
    ) -> None:
        """阈值比较；``value`` 为 None 时记未知。"""
        if value is None:
            verdict.unknown += 1
            return
        if op == ">=":
            condition = value >= threshold
        elif op == "<=":
            condition = value <= threshold
        elif op == ">":
            condition = value > threshold
        elif op == "<":
            condition = value < threshold
        elif op == "==":
            condition = value == threshold
        else:
            raise ValueError(f"未知操作符: {op}")
        _check(condition, reason)

    # ── 交易规则 ───────────────────────────────────────────────────────
    if require_hk_connect:
        _check(result.is_hk_connect, "不在港股通清单（或清单获取失败）")
    _check(result.is_hkd, "非港币计价")
    _check_value(
        result.listing_days,
        HK_TRADE_RULES["min_listing_days"],
        ">=",
        f"上市时长不足{HK_TRADE_RULES['min_listing_days']}天",
    )
    _check(result.is_common_stock, "非普通股标的")
    _check_value(
        result.suspension_days_30d,
        HK_TRADE_RULES["max_suspension_days_30d"],
        "<=",
        f"近30日停牌天数超{HK_TRADE_RULES['max_suspension_days_30d']}天",
    )
    _check(
        None if result.has_risk_warning is None else not result.has_risk_warning,
        "存在监管风险警示",
    )

    # ── 流动性与市值 ───────────────────────────────────────────────────
    _check_value(
        result.turnover_20d_avg,
        HK_LIQUIDITY_RULES["min_avg_turnover_20d"],
        ">=",
        f"近20日日均成交额低于{HK_LIQUIDITY_RULES['min_avg_turnover_20d']:.0f}港元",
    )
    _check_value(
        result.turnover_rate_12m_avg,
        HK_LIQUIDITY_RULES["min_avg_turnover_rate_12m"],
        ">=",
        f"近12个月日均换手率低于{HK_LIQUIDITY_RULES['min_avg_turnover_rate_12m']}%",
    )
    _check_value(
        result.market_cap,
        HK_LIQUIDITY_RULES["min_market_cap"],
        ">=",
        f"总市值低于{HK_LIQUIDITY_RULES['min_market_cap'] / 1e8:.0f}亿港元",
    )

    # ── 财务基本面 ─────────────────────────────────────────────────────
    _check(result.non_gaap_profit_2y, "近2年扣非盈利非正")
    _check(result.clean_audit_2y, "近2年审计意见非标准无保留")
    _check_value(
        result.debt_to_asset,
        HK_FUNDAMENTAL_RULES["max_debt_to_asset"],
        "<=",
        f"资产负债率超{HK_FUNDAMENTAL_RULES['max_debt_to_asset']:.0%}",
    )
    _check(result.operating_cashflow_3y, "近3年经营现金流累计非正")
    _check(result.revenue_growth_3y, "近3年营收连续负增长")
    _check_value(
        result.gross_margin,
        HK_FUNDAMENTAL_RULES["min_gross_margin"],
        ">=",
        f"主业毛利率低于{HK_FUNDAMENTAL_RULES['min_gross_margin']:.0%}",
    )
    _check_value(
        result.avg_roe_3y,
        HK_FUNDAMENTAL_RULES["min_avg_roe_3y"],
        ">=",
        f"近3年平均ROE低于{HK_FUNDAMENTAL_RULES['min_avg_roe_3y']:.0%}",
    )

    # ── 庄股与异常 ─────────────────────────────────────────────────────
    _check_value(
        result.top_shareholder_pct,
        HK_ABNORMAL_RULES["max_top_shareholder_concentration"],
        "<=",
        f"第一大股东+一致行动人持股超{HK_ABNORMAL_RULES['max_top_shareholder_concentration']:.0%}",
    )
    _check_value(
        result.max_drawdown_12m,
        HK_ABNORMAL_RULES["max_drawdown_12m"],
        "<=",
        f"近12个月最大回撤超{HK_ABNORMAL_RULES['max_drawdown_12m']:.0%}",
    )
    _check_value(
        result.rights_issues_12m,
        HK_ABNORMAL_RULES["max_rights_issues_12m"],
        "<=",
        f"近12个月供股/合股次数超{HK_ABNORMAL_RULES['max_rights_issues_12m']}次",
    )
    _check_value(
        result.price,
        HK_ABNORMAL_RULES["min_price"],
        ">=",
        f"最新股价低于{HK_ABNORMAL_RULES['min_price']}港元",
    )
    _check_value(
        result.drawdown_from_high,
        HK_ABNORMAL_RULES["max_drawdown_from_high"],
        "<=",
        f"相对历史高点跌幅超{HK_ABNORMAL_RULES['max_drawdown_from_high']:.0%}",
    )
    _check_value(
        result.single_day_spike_1y,
        HK_ABNORMAL_RULES["max_single_day_spike_1y"],
        "<=",
        f"近1年无基本面支撑单日涨幅超{HK_ABNORMAL_RULES['max_single_day_spike_1y']:.0%}",
    )

    return verdict


def normalize_dividend_yield(value: float | None) -> float | None:
    """把股息率统一为比例（``0.04`` = 4%）。

    数据源可能返回百分数（``1.56`` 表示 1.56%）或比例（``0.0156``），
    按「大于 1 视为百分数」归一化。

    Args:
        value: 原始股息率。

    Returns:
        比例形式的股息率；``None`` 或 NaN 返回 ``None``。
    """
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(v):
        return None
    if v > 1.0:
        v = v / 100.0
    return v


def compute_quality_score(result: HKScreenResult) -> float:
    """计算质量加分得分（0-100），仅用于排序，不参与硬过滤。

    评分项（合计 100）：
    - 近 3 年连续派息 15
    - 派息比率在 (0, 100%] 10
    - 股息率 >= 4% 15
    - 年化波动率 <= 30% 15
    - FCF/净利润 >= 80% 15
    - PE(TTM) <= 15 得 20；15 < PE <= 25 得 10

    Args:
        result: 筛选结果。

    Returns:
        0-100 的质量分。
    """
    score = 0.0

    if result.dividend_3y:
        score += 15.0

    if result.payout_ratio is not None and 0.0 < result.payout_ratio <= 1.0:
        score += 10.0

    yield_ratio = normalize_dividend_yield(result.dividend_yield)
    if yield_ratio is not None and yield_ratio >= HK_QUALITY_RULES["min_dividend_yield_ttm"]:
        score += 15.0

    if (
        result.annualized_volatility_12m is not None
        and result.annualized_volatility_12m <= HK_QUALITY_RULES["max_annualized_volatility_12m"]
    ):
        score += 15.0

    if (
        result.fcf_to_net_profit is not None
        and result.fcf_to_net_profit >= HK_QUALITY_RULES["min_fcf_to_net_profit"]
    ):
        score += 15.0

    if result.pe_ttm is not None and 0 < result.pe_ttm <= HK_QUALITY_RULES["max_pe_ttm"]:
        score += 20.0
    elif result.pe_ttm is not None and HK_QUALITY_RULES["max_pe_ttm"] < result.pe_ttm <= 25.0:
        score += 10.0

    return min(score, 100.0)


def _fmt_pe(val: float | None) -> str:
    """安全格式化 PE_TTM（None/NaN -> ``N/A``）。"""
    if val is None:
        return "N/A"
    try:
        v = float(val)
    except (TypeError, ValueError):
        return "N/A"
    if pd.isna(v):
        return "N/A"
    return f"{v:.1f}"


def _fmt_roe(val: float | None) -> str:
    """安全格式化 ROE（None/NaN -> ``N/A``）。"""
    if val is None:
        return "N/A"
    try:
        v = float(val)
    except (TypeError, ValueError):
        return "N/A"
    if pd.isna(v):
        return "N/A"
    return f"{v:.1%}"


def _fmt_cap(val: float | None) -> str:
    """安全格式化总市值（港元 -> 亿）。"""
    if val is None:
        return "N/A"
    try:
        v = float(val)
    except (TypeError, ValueError):
        return "N/A"
    if pd.isna(v):
        return "N/A"
    return f"{v / 1e8:.0f}亿"


def screen_hk_stocks(
    results: list[HKScreenResult],
    min_pass_rate: float = 1.0,
    require_hk_connect: bool = False,
    min_coverage: float = 0.0,
) -> list[HKScreenResult]:
    """执行规则判定并返回通过者（按质量分降序）。

    Args:
        results: 待筛选列表（会被就地写入 ``pass_count`` / ``passed`` 等字段）。
        min_pass_rate: 已评估规则的最小通过比例（1.0 = 全通过）。
        require_hk_connect: 是否强制港股通资格。
        min_coverage: 数据完整度门槛（已评估 / 全部规则）。``0`` 表示不限制。

    Returns:
        通过的股票列表，按质量分、通过数降序排列。
    """
    passed_results: list[HKScreenResult] = []

    for r in results:
        verdict = count_passed_rules(r, require_hk_connect=require_hk_connect)
        r.pass_count = verdict.passed
        r.total_rules = verdict.evaluated
        r.unknown_count = verdict.unknown
        r.coverage = verdict.coverage
        r.fail_reasons = verdict.fail_reasons

        r.passed = verdict.pass_rate >= min_pass_rate and verdict.coverage >= min_coverage
        if r.passed:
            passed_results.append(r)

    passed_results.sort(
        key=lambda r: (
            -compute_quality_score(r),
            -r.pass_count,
            r.unknown_count,
            len(r.fail_reasons),
        )
    )
    return passed_results


def print_screen_summary(results: list[HKScreenResult]) -> None:
    """打印筛选摘要。

    注意：入参必须是**全量**筛选结果，函数内部自行统计通过数，
    否则通过率会恒为 100%。

    Args:
        results: 全量筛选结果（已由 :func:`screen_hk_stocks` 写入 ``passed``）。
    """
    total = len(results)
    passed = [r for r in results if r.passed]
    unknown_avg = sum(r.unknown_count for r in results) / total if total else 0.0
    coverage_avg = sum(r.coverage for r in results) / total if total else 0.0

    logger.info("=" * 60)
    logger.info("港股筛选结果摘要")
    logger.info("=" * 60)
    logger.info(f"候选股票总数: {total} 只")
    logger.info(f"通过筛选股票数: {len(passed)} 只")
    rate = len(passed) / total * 100 if total else 0.0
    logger.info(f"通过率: {rate:.1f}%")
    logger.info(f"数据完整度: 平均 {coverage_avg:.1%}（平均未知规则 {unknown_avg:.1f} 条）")
    logger.info("=" * 60)

    if passed:
        logger.info("\n前 10 名通过筛选的股票:")
        for i, r in enumerate(passed[:10], 1):
            logger.info(
                f"  {i:2d}. {r.ticker} {r.name:<20} "
                f"市值={_fmt_cap(r.market_cap)} "
                f"PE={_fmt_pe(r.pe_ttm)} "
                f"ROE={_fmt_roe(r.avg_roe_3y)} "
                f"质量分={compute_quality_score(r):.0f} "
                f"通过规则={r.pass_count}/{r.total_rules} "
                f"未知={r.unknown_count}"
            )
    logger.info("=" * 60)
