"""
Stock Filter — 股票过滤规则引擎。

职责：
  · 多条件规则链（置信度 / 信号 / 市值 / 行业 / PE / PB / 风险分 / 成交量）
  · 支持白名单 / 黑名单 / 范围 / 子串匹配等操作符
  · 从 baostock 批量获取过滤所需的元数据（PE / PB / 行业 / 市值）

使用方式：
    rules = [
        MinValueRule("ta_confidence", 55.0),
        InSetRule("signal", {"BUY", "HOLD"}),
        RangeRule("market_cap_billion", 50.0, 5000.0),
        SubstrRule("industry", "银行"),
        MaxValueRule("risk_score", 0.7),
    ]
    passed = StockFilter(rules).apply(stock_meta)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from loguru import logger

# ── 操作符枚举 ────────────────────────────────────────────────────────────────


class FilterOp(str, Enum):
    """过滤操作符。"""

    # 范围类
    MIN = "min"  # ≥ value（字段值 >= 下限）
    MAX = "max"  # ≤ value（字段值 <= 上限）
    RANGE = "range"  # [low, high]
    # 集合类
    IN = "in"  # 在白名单中
    NOT_IN = "not_in"  # 不在黑名单中
    # 子串类
    CONTAINS = "contains"  # 字段包含子串（行业名模糊匹配）
    # 正则类
    MATCH = "match"  # 字段匹配正则表达式


# ── 规则基类与具体规则 ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FilterRule:
    """单条过滤规则（不可变）。"""

    field: str  # 字段名（在 StockMeta 中）
    op: FilterOp
    value: object  # 比较值（float / set / str / tuple）
    label: str = ""  # 人类可读描述（用于日志）

    def __post_init__(self):
        if not self.label:
            self.label = f"{self.field} {self.op.value}"


class MinValueRule(FilterRule):
    """字段值 >= value。"""

    def __init__(self, field: str, value: float, label: str = ""):
        super().__init__(field=field, op=FilterOp.MIN, value=value, label=label or f">={value}")


class MaxValueRule(FilterRule):
    """字段值 <= value。"""

    def __init__(self, field: str, value: float, label: str = ""):
        super().__init__(field=field, op=FilterOp.MAX, value=value, label=label or f"<={value}")


class RangeRule(FilterRule):
    """字段值在 [low, high] 范围内。"""

    def __init__(self, field: str, low: float, high: float, label: str = ""):
        super().__init__(
            field=field,
            op=FilterOp.RANGE,
            value=(low, high),
            label=label or f"[{low}, {high}]",
        )


class InSetRule(FilterRule):
    """字段值在集合中（白名单）。"""

    def __init__(self, field: str, values: set, label: str = ""):
        super().__init__(
            field=field, op=FilterOp.IN, value=frozenset(values), label=label or f"IN {values}"
        )


class NotInSetRule(FilterRule):
    """字段值不在集合中（黑名单）。"""

    def __init__(self, field: str, values: set, label: str = ""):
        super().__init__(
            field=field,
            op=FilterOp.NOT_IN,
            value=frozenset(values),
            label=label or f"NOT_IN {values}",
        )


class ContainsRule(FilterRule):
    """字段包含指定子串。"""

    def __init__(self, field: str, substr: str, label: str = ""):
        super().__init__(
            field=field, op=FilterOp.CONTAINS, value=substr, label=label or f"contains '{substr}'"
        )


class MatchRule(FilterRule):
    """字段匹配正则表达式。"""

    def __init__(self, field: str, pattern: str, label: str = ""):
        super().__init__(
            field=field,
            op=FilterOp.MATCH,
            value=re.compile(pattern),
            label=label or f"matches '{pattern}'",
        )


# ── 股票元数据 ────────────────────────────────────────────────────────────────


@dataclass
class StockMeta:
    """
    股票过滤所需的全部元数据字段。

    所有字段均可选（None 表示该字段未获取到数据，跳过对应规则的过滤检查）。
    """

    # TA / Kronos 产物
    signal: Optional[str] = None  # BUY / HOLD / SELL
    confidence: Optional[float] = None  # 置信度 0–100
    risk_score: Optional[float] = None  # 风险分 0–1

    # 基本面（来自 baostock / 外部数据源）
    pe_ttm: Optional[float] = None  # 市盈率（TTM）
    pb: Optional[float] = None  # 市净率
    market_cap_billion: Optional[float] = None  # 总市值（亿元）
    volume_ratio: Optional[float] = None  # 量比（当日成交量 / 5日均量）
    turnover_rate: Optional[float] = None  # 换手率（%）

    # 行业 / 概念
    industry: Optional[str] = None  # 行业名称（如 "银行"、"食品饮料"）
    industry_code: Optional[str] = None  # 行业代码（如 "B61"）
    concept: Optional[str] = None  # 概念板块名称

    # 其他
    is_st: bool = False  # 是否 ST 标的
    ticker: str = ""  # 股票代码（用于日志）

    # ── 异常标记（由 abnormal_stock.py 预检填充）────────────────
    abnormal_flags: list[str] = field(default_factory=list)  # ["ST", "SUSPENDED", ...]
    abnormality_score: float = 0.0  # 综合异常严重程度 0.0–1.0


# ── 过滤引擎 ─────────────────────────────────────────────────────────────────


class StockFilter:
    """
    股票过滤引擎。

    按规则链顺序逐一检查，任一规则不通过即被过滤。
    None 字段不参与相关规则的匹配（视为通过）。

    Parameters
    ----------
    rules : list[FilterRule]
        过滤规则列表，按优先级顺序排列。
    """

    def __init__(self, rules: list[FilterRule] | None = None):
        self.rules: list[FilterRule] = rules or []

    # ── 主入口 ─────────────────────────────────────────────────────────────

    def apply(self, meta: StockMeta) -> bool:
        """
        对单只股票的元数据应用所有过滤规则。

        Returns
        -------
        True  = 通过所有规则（保留）
        False = 任一规则未通过（过滤）
        """
        for rule in self.rules:
            try:
                if not self._check_rule(meta, rule):
                    logger.debug(
                        f"🚫 {meta.ticker} 被过滤: {rule.label}"
                        f" (field={rule.field}, value={meta.__dict__.get(rule.field)})"
                    )
                    return False
            except Exception as e:
                logger.warning(f"⚠️  {meta.ticker} 过滤规则异常: {rule.label} — {e}")
        return True

    def apply_batch(self, metas: list[StockMeta]) -> tuple[list[StockMeta], list[StockMeta]]:
        """
        批量过滤。

        Returns
        -------
        (passed, rejected)
        """
        passed: list[StockMeta] = []
        rejected: list[StockMeta] = []
        for m in metas:
            if self.apply(m):
                passed.append(m)
            else:
                rejected.append(m)
        logger.info(f"📋 股票池过滤: {len(metas)} → 通过 {len(passed)}，过滤 {len(rejected)}")
        return passed, rejected

    # ── 规则检查 ───────────────────────────────────────────────────────────

    @staticmethod
    def _check_rule(meta: StockMeta, rule: FilterRule) -> bool:
        """根据操作符检查单条规则。"""
        actual = getattr(meta, rule.field, None)

        # None 字段：跳过该规则（不拦截）
        if actual is None:
            return True

        op = rule.op
        val = rule.value

        if op == FilterOp.MIN:
            return float(actual) >= float(val)  # type: ignore[operator]
        elif op == FilterOp.MAX:
            return float(actual) <= float(val)  # type: ignore[operator]
        elif op == FilterOp.RANGE:
            low, high = val  # type: ignore[misc]
            fv = float(actual)
            return float(low) <= fv <= float(high)  # type: ignore[operator]
        elif op == FilterOp.IN:
            return str(actual) in val  # type: ignore[operator]
        elif op == FilterOp.NOT_IN:
            return str(actual) not in val  # type: ignore[operator]
        elif op == FilterOp.CONTAINS:
            return val in str(actual)  # type: ignore[operator]
        elif op == FilterOp.MATCH:
            return bool(val.search(str(actual)))  # type: ignore[union-attr]
        else:
            return True  # 未知操作符：不拦截

    # ── 便捷工厂方法 ───────────────────────────────────────────────────────

    @classmethod
    def from_config(
        cls,
        min_confidence: float = 55.0,
        allowed_signals: tuple[str, ...] = ("BUY", "HOLD"),
        market_cap_range: tuple[float, float] | None = None,
        industry_whitelist: list[str] | None = None,
        industry_blacklist: list[str] | None = None,
        pe_range: tuple[float, float] | None = None,
        pb_range: tuple[float, float] | None = None,
        max_risk_score: float | None = None,
        min_volume_ratio: float | None = None,
        min_turnover_rate: float | None = None,
        exclude_st: bool = True,
    ) -> "StockFilter":
        """
        从配置参数构建 StockFilter。

        所有参数均为可选；None 表示不添加对应规则。
        """
        rules: list[FilterRule] = []

        # ── 置信度 / 信号（基础过滤）────────────────────────────────────
        rules.append(
            MinValueRule("confidence", min_confidence, label=f"confidence >= {min_confidence}")
        )
        rules.append(
            InSetRule("signal", set(allowed_signals), label=f"signal IN {allowed_signals}")
        )

        # ── ST 过滤 ─────────────────────────────────────────────────────
        if exclude_st:
            rules.append(MaxValueRule("is_st", 0, label="not ST"))

        # ── 市值范围 ────────────────────────────────────────────────────
        if market_cap_range:
            low, high = market_cap_range
            rules.append(
                RangeRule("market_cap_billion", low, high, label=f"market_cap [{low}, {high}]亿")
            )

        # ── 行业白名单：使用 IN 操作符，与 StockMeta.industry 精确匹配 ─
        if industry_whitelist:
            rules.append(
                InSetRule(
                    "industry", set(industry_whitelist), label=f"industry IN {industry_whitelist}"
                )
            )

        if industry_blacklist:
            rules.append(
                NotInSetRule(
                    "industry",
                    set(industry_blacklist),
                    label=f"industry NOT IN {industry_blacklist}",
                )
            )

        # ── PE / PB 范围 ────────────────────────────────────────────────
        if pe_range:
            rules.append(
                RangeRule(
                    "pe_ttm", pe_range[0], pe_range[1], label=f"PE [{pe_range[0]}, {pe_range[1]}]"
                )
            )
        if pb_range:
            rules.append(
                RangeRule(
                    "pb", pb_range[0], pb_range[1], label=f"PB [{pb_range[0]}, {pb_range[1]}]"
                )
            )

        # ── 风险分上限 ──────────────────────────────────────────────────
        if max_risk_score is not None:
            rules.append(
                MaxValueRule("risk_score", max_risk_score, label=f"risk_score <= {max_risk_score}")
            )

        # ── 成交量 / 换手率 ─────────────────────────────────────────────
        if min_volume_ratio is not None:
            rules.append(
                MinValueRule(
                    "volume_ratio", min_volume_ratio, label=f"volume_ratio >= {min_volume_ratio}"
                )
            )
        if min_turnover_rate is not None:
            rules.append(
                MinValueRule(
                    "turnover_rate",
                    min_turnover_rate,
                    label=f"turnover_rate >= {min_turnover_rate}%",
                )
            )

        return cls(rules=rules)


# ── baostock 元数据获取 ───────────────────────────────────────────────────────


def fetch_stock_meta(
    tickers: list[str],
    date: str,
) -> dict[str, StockMeta]:
    """
    批量获取股票的过滤元数据（PE / PB / 行业 / 市值）。

    Parameters
    ----------
    tickers : list[str]
        股票代码列表（如 ["sh.600519", "sz.000858"]）
    date : str
        评估日期（YYYY-MM-DD）

    Returns
    -------
    dict[ticker, StockMeta]
        ticker → StockMeta 映射，缺失字段为 None
    """
    # 懒加载 baostock，避免无环境时直接报错
    try:
        import baostock as bs  # type: ignore
    except ImportError:
        logger.warning("baostock 未安装，跳过元数据获取")
        return {t: StockMeta(ticker=t) for t in tickers}

    metas: dict[str, StockMeta] = {}

    lg = bs.login()
    if lg.error_code != "0":
        logger.warning(f"baostock 登录失败: {lg.error_msg}，跳过元数据获取")
        return {t: StockMeta(ticker=t) for t in tickers}

    try:
        for ticker in tickers:
            meta = StockMeta(ticker=ticker)

            # ── 行业分类（query_stock_industry）─────────────────────────
            rs_ind = bs.query_stock_industry(code=ticker)
            if rs_ind.error_code == "0":
                rows = []
                while rs_ind.next():
                    rows.append(rs_ind.get_row_data())
                if rows:
                    meta.industry = rows[0][1] if len(rows[0]) > 1 else None  # code_name
                    meta.industry_code = rows[0][0] if rows[0] else None

            # ── 财务指标（query_stock_performance）───────────────────────
            # pe、pb 等字段需在行情数据中获取
            rs_perf = bs.query_stock_performance(code=ticker)
            if rs_perf.error_code == "0":
                rows = []
                while rs_perf.next():
                    rows.append(rs_perf.get_row_data())
                if rows:
                    row = rows[-1]  # 最新一期
                    # baostock performance 字段顺序：
                    # 0=code, 1=report_date, 2=pe_ttm, 3=pb, ...
                    if len(row) > 2 and row[2]:
                        try:
                            meta.pe_ttm = float(row[2])
                        except (ValueError, TypeError):
                            pass
                    if len(row) > 3 and row[3]:
                        try:
                            meta.pb = float(row[3])
                        except (ValueError, TypeError):
                            pass

            # ── 市值（query_stock_basic，部分版本支持）──────────────────
            rs_basic = bs.query_stock_basic(code=ticker)
            if rs_basic.error_code == "0":
                while rs_basic.next():
                    row = rs_basic.get_row_data()
                    # row: [code, code_name, ipoDate, outDate, ...]
                    # 市值字段不在 basic 中，需从行情接口获取
                    pass

            metas[ticker] = meta

    finally:
        bs.logout()  # type: ignore

    return metas
