"""six_dim 选股 — 估值维度。

负责获取五年 PE/PB 分位，并执行估值维度的门槛 + 打分。
"""

from __future__ import annotations

from scripts.six_dim.constants import MAX_PB, MAX_PE, MAX_PE_PCT, MAX_PEG


def fetch_valuation_history(
    codes: list[str], date_str: str | None = None
) -> dict[str, dict[str, float]]:
    """获取五年 PE/PB 分位和 PEG。

    此处为桩实现：实际估值数据通过 akshare 联网获取，由调用方在
    ``screen()`` 中注入。保持该函数签名以便后续替换为真实数据源。

    Args:
        codes: 股票代码列表（6 位裸代码）。
        date_str: 分析日期。

    Returns:
        代码 → 估值字典的映射。
    """
    # TODO: 接入 akshare.stock_zh_valuation_baidu 获取五年 PE/PB 分位
    return {}


def score_valuation(
    pe: float | None,
    pb: float | None,
    hist: dict[str, float] | None,
    profit_cagr: float,
) -> tuple[bool, float, str]:
    """估值维度门槛 + 打分。

    Args:
        pe: PE_TTM。
        pb: PB。
        hist: 五年估值历史（含 pe_pct, pb_pct, peg 等字段）。
        profit_cagr: 净利润 CAGR（%），用于计算 PEG。

    Returns:
        ``(是否通过, 0~100 分, 淘汰原因或通过说明)``。
    """
    if pe is None or not (0 < pe < MAX_PE):
        return False, 0.0, f"PE={pe} 不在 (0, {MAX_PE})"
    if pb is None or not (0 < pb < MAX_PB):
        return False, 0.0, f"PB={pb} 不在 (0, {MAX_PB})"

    pe_pct = hist.get("pe_pct") if hist else None
    pb_pct = hist.get("pb_pct") if hist else None
    peg = hist.get("peg") if hist else None
    if peg is None and profit_cagr > 0:
        peg = pe / profit_cagr

    if pe_pct is not None and pe_pct > MAX_PE_PCT:
        return False, 0.0, f"PE 五年分位 {pe_pct:.0f}% > {MAX_PE_PCT:.0f}%"
    if peg is not None and peg > MAX_PEG:
        return False, 0.0, f"PEG={peg:.2f} > {MAX_PEG}"

    # 打分：PE 分位占 40%、PB 分位占 30%、PEG 占 30%
    pe_score = (100 - (pe_pct or 50)) / 100 * 40 if pe_pct is not None else 20
    pb_score = (100 - (pb_pct or 50)) / 100 * 30 if pb_pct is not None else 15
    peg_score = max(0, (MAX_PEG - (peg or MAX_PEG)) / MAX_PEG * 30) if peg is not None else 15
    score = pe_score + pb_score + peg_score
    detail = f"PE={pe:.1f} PB={pb:.2f}"
    if pe_pct is not None:
        detail += f" PE分位{pe_pct:.0f}%"
    if peg is not None:
        detail += f" PEG={peg:.2f}"
    return True, round(score, 1), detail
