"""six_dim 选股 — 飞书汇总卡片。

构建六维选股的飞书推送文本：Top 10 只展开完整明细，其余只列名称与代码。
"""

from __future__ import annotations

from scripts.six_dim.constants import TIER_WIDTH
from scripts.six_dim.models import StockScore


def build_feishu_card(
    results: list[StockScore],
    date_str: str,
    funnel: dict[str, int],
    rest: list[StockScore] | None = None,
) -> str:
    """构建六维选股飞书汇总卡片。

    top 10 只展开完整明细（含各维度分 + 核心指标）；其余候选只列出
    股票名称与代码，不展开明细。

    Args:
        results: 排序后的推荐列表（前 10 只，含完整明细）。
        date_str: 日期字符串（YYYY-MM-DD）。
        funnel: :func:`screen` 返回的各关卡人数统计。
        rest: 其余候选（未进入 top 10 的筛选中），只列名称 + 代码。

    Returns:
        飞书文本卡片正文。
    """
    lines: list[str] = []
    lines.append(f"📊 **{date_str} 六维选股**")
    lines.append(
        f"排序：分档字典序（每维 {TIER_WIDTH:.0f} 分一档，"
        "逻辑 > 估值 > 位置 > 趋势 > 股东人数 > 量价；综合分仅同档兜底，非排名依据）"
    )
    lines.append("")
    if funnel:
        lines.append(
            f"候选漏斗：A股 {funnel.get('universe', 0)} → "
            f"逻辑 {funnel.get('logic_pass', 0)} → "
            f"绝对估值 {funnel.get('abs_val_pass', 0)} → "
            f"分位/PEG {funnel.get('val_pass', 0)} → "
            f"趋势 {funnel.get('trend_pass', 0)} → 精选 {len(results)}"
        )
        lines.append("")
    for i, r in enumerate(results, 1):
        m = r.metrics
        lines.append(f"{i}. {r.name}（{r.code}）- 逻辑{r.logic:.1f}分 综合{r.composite:.1f}分")
        lines.append(
            f"   逻辑{r.logic:.0f} 估值{r.valuation:.0f} 位置{r.position:.0f} "
            f"趋势{r.trend:.0f} 股东{r.holder:.0f} 量价{r.vol_price:.0f}"
        )
        pe_pct = m.get("pe_pct")
        pe_txt = (
            f"PE{m.get('pe')}({pe_pct:.0f}%分位)"
            if isinstance(pe_pct, (int, float))
            else f"PE{m.get('pe')}"
        )
        lines.append(
            f"   {pe_txt} PB{m.get('pb')} ROE{m.get('roe_avg')}% "
            f"净利CAGR{m.get('profit_cagr')}% 市值{m.get('market_cap_yi')}亿"
        )
        pos = m.get("pct_pos_3y")
        slope = m.get("ma60_slope")
        amount_ratio = m.get("amount_ratio")
        holder = m.get("holder_chg_pct")
        pos_txt = f"{pos}" if isinstance(pos, (int, float)) else "-"
        slope_txt = f"{slope:+.1f}" if isinstance(slope, (int, float)) else "-"
        ratio_txt = f"{amount_ratio}" if isinstance(amount_ratio, (int, float)) else "-"
        holder_txt = (
            f"股东户数{holder:+.1f}%" if isinstance(holder, (int, float)) else "股东户数缺失"
        )
        lines.append(
            f"   位置3年{pos_txt}% | MA60斜率{slope_txt}% | 成交额{ratio_txt}× | {holder_txt}"
        )
        lines.append("")
    if rest:
        lines.append("🆕 **其余候选（仅列出名称，不展开明细）**")
        lines.append("")
        for i, r in enumerate(rest, start=len(results) + 1):
            lines.append(f"{i}. {r.name}（{r.code}）")
        lines.append("")
    lines.append("⚠️ 量化筛选结果，不含个股深度尽调，不构成投资建议。")
    return "\n".join(lines)
