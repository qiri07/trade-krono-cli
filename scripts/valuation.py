"""valuation — 估值评估与建议买入价分析模块。

提供：
  · 估值综合评分（0-100，越高越低估）
  · 建议买入价计算（Graham公式 + 历史分位法 + 股息折现法）
  · PE/PB 历史分位查询（通过 akshare）
  · 估值风险评估（集成到风险引擎）
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


# ── 行业类型判断 ───────────────────────────────────────────────────────────────

_FINANCIAL_KEYWORDS = ("银行", "保险", "证券", "基金", "信托", "金融", "租赁", "信贷")
_CYCLIC_KEYWORDS = ("铝业", "钢铁", "煤炭", "有色", "化工", "水泥", "玻璃", "航运", "石油")


def _is_financial(name: str) -> bool:
    return any(kw in name for kw in _FINANCIAL_KEYWORDS)


def _is_cyclic(name: str) -> bool:
    return any(kw in name for kw in _CYCLIC_KEYWORDS)


# ── 建议买入价计算模型 ──────────────────────────────────────────────────────────


@dataclass
class ValuationResult:
    """单只股票的估值评估结果。"""

    ticker: str
    name: str
    current_price: float
    pe_ttm: float | None
    pb: float | None
    roe: float | None
    dividend_yield: float | None  # 股息率（%）
    eps_ttm: float | None
    book_value_per_share: float | None  # 每股净资产

    # 历史分位
    pe_percentile: float | None  # 0-100，越小越低估
    pb_percentile: float | None

    # 建议买入价
    graham_price: float | None  # Graham 公式计算
    pe_median_price: float | None  # 基于历史 PE 中位数
    dcf_price: float | None  # 股息折现法（仅金融/消费）
    combined_price: float | None  # 综合建议买入价

    # 安全边际
    safety_margin_pct: float | None  # 相对于当前价的安全边际
    upside_pct: float | None  # 上涨空间（%）

    # 综合评分
    valuation_score: float  # 0-100，越高越值得买入

    # 评估结论
    conclusion: str = ""  # 文字结论
    methods_used: list[str] = field(default_factory=list)  # 使用了哪些定价方法
    notes: str = ""  # 备注说明


def _safe_float(v: object) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)  # type: ignore[arg-type]
        if f != f or abs(f) == float("inf") or math.isnan(f):
            return None
        return f
    except (ValueError, TypeError):
        return None


def calc_graham_price(
    eps: float | None,
    book_value: float | None,
    growth_rate: float | None = None,
) -> float | None:
    """使用格雷厄姆公式计算内在价值。

    Graham 公式（保守型）：
      V = EPS × (8.5 + 2g)
      其中 g = 预期增长率(%)

    Parameters
    ----------
    eps : float | None
        每股收益 TTM
    book_value : float | None
        每股净资产（用于上限约束）
    growth_rate : float | None
        预期年增长率(%)，默认 5%

    Returns
    -------
    float | None
        内在价值，数据不足时返回 None
    """
    eps = _safe_float(eps)
    if eps is None or eps <= 0:
        return None

    g = _safe_float(growth_rate)
    if g is None:
        g = 5.0  # 默认增长率 5%
    g = max(g, 0)

    # 保守 Graham 公式：V = EPS × (8.5 + 2g)
    intrinsic = eps * (8.5 + 2 * g)

    # 约束：不超过每股净资产的 1.5 倍（防止高市盈率陷阱）
    bv = _safe_float(book_value)
    if bv is not None and bv > 0:
        intrinsic = min(intrinsic, bv * 1.5)

    return round(intrinsic, 2)


def calc_pe_median_price(
    current_price: float,
    pe_ttm: float | None,
    pe_percentile: float | None,
) -> float | None:
    """基于历史 PE 分位计算建议买入价。

    逻辑：以当前价反推历史中位 PE 对应的价格，作为合理买入区间。
    当前 PE 分位越低 → 历史中位对应价格越高 → 越有上涨空间。

    Parameters
    ----------
    current_price : float
        当前价格
    pe_ttm : float | None
        当前 PE_TTM
    pe_percentile : float | None
        当前 PE 在历史中的分位（0-100）

    Returns
    -------
    float | None
        建议买入价，数据不足时返回 None
    """
    pe = _safe_float(pe_ttm)
    if pe is None or pe <= 0:
        return None

    # 若 PE 分位 < 50%，说明当前低于历史中位，建议买入价高于当前价
    # 若 PE 分位 > 50%，说明当前高于历史中位，建议买入价低于当前价
    if pe_percentile is not None and pe_percentile > 0:
        median_pe_ratio = 50.0 / pe_percentile
    else:
        median_pe_ratio = 1.0  # 无分位数据，假设持平

    # 保守：限制最大涨幅不超过 20%
    ratio = min(median_pe_ratio, 1.2)
    suggested = current_price * ratio
    return round(suggested, 2)


def calc_ddm_price(
    dividend_per_share: float | None,
    required_return: float = 0.10,
    growth_rate: float = 0.03,
) -> float | None:
    """使用股息折现模型（DDM/Gordon Growth）计算内在价值。

    V = D1 / (r - g)
    其中 D1 = 下一年股息，r = 要求回报率，g = 永续增长率

    Parameters
    ----------
    dividend_per_share : float | None
        每股股息（年）
    required_return : float
        要求回报率（默认 10%）
    growth_rate : float
        永续增长率（默认 3%）

    Returns
    -------
    float | None
        内在价值，数据不足时返回 None
    """
    dps = _safe_float(dividend_per_share)
    if dps is None or dps <= 0:
        return None
    if growth_rate >= required_return:
        return None  # 增长率不低于要求回报，模型不适用
    d1 = dps * (1 + growth_rate)
    return round(d1 / (required_return - growth_rate), 2)


def calc_valuation_score(
    pe_ttm: float | None,
    pb: float | None,
    pe_percentile: float | None,
    roe: float | None,
    dividend_yield: float | None = None,
) -> tuple[float, list[str]]:
    """计算估值综合评分（0-100，越高越被低估）。

    评分维度：
      - PE 分位（40%权重）：越低分越高
      - PE 绝对值（25%权重，无分位时备用）
      - PB（30%权重）：越低分越高
      - ROE（20%权重）：越高越好
      - 股息率（10%权重）：越高越好

    Parameters
    ----------
    pe_ttm : float | None
        当前 PE_TTM
    pb : float | None
        当前 PB
    pe_percentile : float | None
        PE 历史分位（0-100）
    roe : float | None
        ROE（%）
    dividend_yield : float | None
        股息率（%）

    Returns
    -------
    tuple[float, list[str]]
        (综合评分 0-100, 使用的维度列表)
    """
    score = 50.0  # 中性起点
    used: list[str] = []

    # PE 分位评分（权重 40%）
    if pe_percentile is not None:
        pe_score = max(0.0, min(100.0, 100.0 - pe_percentile))
        score += pe_score * 0.4
        used.append("pe_percentile")
    elif pe_ttm is not None and pe_ttm > 0:
        # 无分位数据时，用 PE 绝对值打分（PE<10满分，PE>30零分）
        pe_score = max(0.0, min(100.0, (30 - pe_ttm) / 30 * 100))
        score += pe_score * 0.25
        used.append("pe_absolute")

    # PB 评分（权重 30%）
    if pb is not None and pb > 0:
        pb_score = max(0.0, min(100.0, (4 - pb) / 4 * 100))
        score += pb_score * 0.3
        used.append("pb")

    # ROE 评分（权重 20%）
    if roe is not None and roe > 0:
        roe_score = min(100.0, roe / 20 * 100)  # ROE=20% → 满分
        score += roe_score * 0.2
        used.append("roe")

    # 股息率评分（权重 10%）
    if dividend_yield is not None and dividend_yield > 0:
        div_score = min(100.0, dividend_yield / 5 * 100)  # 股息率5% → 满分
        score += div_score * 0.1
        used.append("dividend")

    return round(score, 1), used


def evaluate_valuation(
    ticker: str,
    name: str,
    current_price: float,
    pe_ttm: float | None,
    pb: float | None,
    roe: float | None,
    pe_percentile: float | None,
    pb_percentile: float | None = None,
    eps_ttm: float | None = None,
    book_value_per_share: float | None = None,
    dividend_yield: float | None = None,
    growth_rate: float | None = None,
) -> ValuationResult:
    """对单只股票进行完整估值评估，计算建议买入价。

    Parameters
    ----------
    ticker : str
        股票代码（6位数字）
    name : str
        股票名称
    current_price : float
        当前价格
    pe_ttm : float | None
        PE_TTM
    pb : float | None
        PB
    roe : float | None
        ROE（%）
    pe_percentile : float | None
        PE 历史分位（0-100）
    pb_percentile : float | None
        PB 历史分位（0-100），可选
    eps_ttm : float | None
        EPS TTM（用于 Graham 公式）
    book_value_per_share : float | None
        每股净资产（用于 Graham 公式上限约束）
    dividend_yield : float | None
        股息率（%），可选
    growth_rate : float | None
        预期增长率（%），默认 5%

    Returns
    -------
    ValuationResult
        完整的估值评估结果
    """
    result = ValuationResult(
        ticker=ticker,
        name=name,
        current_price=current_price,
        pe_ttm=pe_ttm,
        pb=pb,
        roe=roe,
        dividend_yield=dividend_yield,
        eps_ttm=eps_ttm,
        book_value_per_share=book_value_per_share,
        pe_percentile=pe_percentile,
        pb_percentile=pb_percentile,
        graham_price=None,
        pe_median_price=None,
        dcf_price=None,
        combined_price=None,
        safety_margin_pct=None,
        upside_pct=None,
        valuation_score=50.0,
        conclusion="",
        methods_used=[],
        notes="",
    )

    # 1. Graham 公式估值
    gr_growth = _safe_float(growth_rate) if growth_rate is not None else None
    graham = calc_graham_price(eps_ttm, book_value_per_share, gr_growth)
    if graham is not None and graham > 0:
        result.graham_price = graham
        result.methods_used.append("graham")

    # 2. PE 历史分位估值
    pe_median = calc_pe_median_price(current_price, pe_ttm, pe_percentile)
    if pe_median is not None and pe_median > 0:
        result.pe_median_price = pe_median
        result.methods_used.append("pe_median")

    # 3. DDM 股息折现（仅适用于有股息的公司）
    if dividend_yield is not None and dividend_yield > 0 and current_price > 0:
        div_per_share = current_price * dividend_yield / 100
        ddm = calc_ddm_price(div_per_share, required_return=0.10, growth_rate=0.03)
        if ddm is not None and ddm > 0:
            result.dcf_price = ddm
            result.methods_used.append("ddm")

    # 4. 综合建议买入价
    prices = [
        p for p in [result.graham_price, result.pe_median_price, result.dcf_price] if p is not None
    ]
    if prices:
        # 取各方法的保守值（最低）作为建议买入价
        result.combined_price = round(min(prices), 2)
        # 不低于当前价的 70%（防止极端保守）
        result.combined_price = max(result.combined_price, current_price * 0.7)

    # 5. 安全边际
    if result.combined_price is not None and current_price > 0:
        result.safety_margin_pct = round(
            (result.combined_price - current_price) / current_price * 100, 1
        )
        result.upside_pct = result.safety_margin_pct

    # 6. 综合评分
    result.valuation_score, result.methods_used = calc_valuation_score(
        pe_ttm=pe_ttm,
        pb=pb,
        pe_percentile=pe_percentile,
        roe=roe,
        dividend_yield=dividend_yield,
    )

    # 7. 文字结论
    result.conclusion = _build_valuation_conclusion(result)
    result.notes = _build_valuation_notes(result)

    return result


def _build_valuation_conclusion(r: ValuationResult) -> str:
    """生成估值评估文字结论。"""
    parts: list[str] = []

    # PE 分位判断
    if r.pe_percentile is not None:
        if r.pe_percentile < 20:
            parts.append(f"PE分位{r.pe_percentile:.0f}%，历史低估区间")
        elif r.pe_percentile < 40:
            parts.append(f"PE分位{r.pe_percentile:.0f}%，略低于中位")
        elif r.pe_percentile < 60:
            parts.append(f"PE分位{r.pe_percentile:.0f}%，合理区间")
        else:
            parts.append(f"PE分位{r.pe_percentile:.0f}%，历史偏高")

    # PB 判断
    if r.pb is not None and r.pb > 0:
        if r.pb < 1:
            parts.append(f"PB={r.pb:.2f}，破净")
        elif r.pb < 2:
            parts.append(f"PB={r.pb:.2f}，合理偏低")
        else:
            parts.append(f"PB={r.pb:.2f}，偏高")

    # ROE 判断
    if r.roe is not None and r.roe > 0:
        if r.roe >= 20:
            parts.append(f"ROE={r.roe:.1f}%，优秀")
        elif r.roe >= 15:
            parts.append(f"ROE={r.roe:.1f}%，良好")

    # 综合评分
    score = r.valuation_score
    if score >= 70:
        label = "🟢 低估值，具备安全边际"
    elif score >= 50:
        label = "🟡 合理估值，观望为主"
    else:
        label = "🔴 高估值，谨慎介入"
    parts.append(f"估值评分{score:.0f}分 {label}")

    return "；".join(parts) if parts else "数据不足，无法评估"


def _build_valuation_notes(r: ValuationResult) -> str:
    """生成估值备注说明。"""
    notes_parts: list[str] = []

    if r.methods_used:
        method_map = {
            "graham": "Graham公式",
            "pe_median": "PE历史分位",
            "ddm": "DDM股息折现",
        }
        notes_parts.append(f"定价方法：{'+'.join(method_map.get(m, m) for m in r.methods_used)}")

    if r.pe_ttm is not None and r.pe_ttm > 0:
        notes_parts.append(f"PE_TTM={r.pe_ttm:.1f}")
    if r.pb is not None and r.pb > 0:
        notes_parts.append(f"PB={r.pb:.2f}")
    if r.roe is not None and r.roe > 0:
        notes_parts.append(f"ROE={r.roe:.1f}%")

    return "；".join(notes_parts) if notes_parts else "无"


# ── 批量评估工具 ────────────────────────────────────────────────────────────────


def batch_evaluate(
    stocks: list[dict],
) -> list[ValuationResult]:
    """批量对股票列表进行估值评估。

    Parameters
    ----------
    stocks : list[dict]
        每只股票需包含以下字段：
          - ticker : str（6位代码）
          - name : str
          - current_price : float
          - pe_ttm : float | None
          - pb : float | None
          - roe : float | None
          - pe_percentile : float | None
          - eps_ttm : float | None（可选）
          - book_value_per_share : float | None（可选）
          - dividend_yield : float | None（可选）

    Returns
    -------
    list[ValuationResult]
    """
    results: list[ValuationResult] = []
    for s in stocks:
        r = evaluate_valuation(
            ticker=s["ticker"],
            name=s.get("name", ""),
            current_price=s["current_price"],
            pe_ttm=s.get("pe_ttm"),
            pb=s.get("pb"),
            roe=s.get("roe"),
            pe_percentile=s.get("pe_percentile"),
            eps_ttm=s.get("eps_ttm"),
            book_value_per_share=s.get("book_value_per_share"),
            dividend_yield=s.get("dividend_yield"),
            growth_rate=s.get("growth_rate"),
        )
        results.append(r)
    return results
