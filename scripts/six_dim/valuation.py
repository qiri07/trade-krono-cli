"""six_dim 选股 — 估值维度。

负责获取五年 PE/PB 分位，并执行估值维度的门槛 + 打分。
"""

from __future__ import annotations

import pandas as pd
from loguru import logger

from scripts.six_dim.constants import MAX_PB, MAX_PE, MAX_PE_PCT, MAX_PEG


def fetch_valuation_history(
    codes: list[str], date_str: str | None = None
) -> dict[str, dict[str, float]]:
    """获取五年 PE/PB 分位。

    通过 akshare.stock_zh_valuation_baidu 获取每只股票近五年估值序列，
    计算 PE/PB 五年分位（百分位排名，0~100）。

    Args:
        codes: 股票代码列表（6 位裸代码）。
        date_str: 分析日期（保留签名兼容性，实际不使用）。

    Returns:
        代码 → 估值字典的映射，包含 ``pe_pct`` / ``pb_pct`` 字段。
    """
    import akshare as ak

    result: dict[str, dict[str, float]] = {}
    for code in codes:
        try:
            df = ak.stock_zh_valuation_baidu(code=code)
            if df is None or df.empty:
                continue
            # 百度估值接口返回中文列名，常见为 "日期"、"PE"、"PB" 等
            pe_col: str | None = None
            pb_col: str | None = None
            for col in df.columns:
                c = str(col).upper().strip()
                if c == "PE":
                    pe_col = str(col)
                elif c == "PB":
                    pb_col = str(col)
            if pe_col is None or pb_col is None:
                continue
            pe_series = pd.to_numeric(df[pe_col], errors="coerce").dropna()
            pb_series = pd.to_numeric(df[pb_col], errors="coerce").dropna()
            if pe_series.empty or pb_series.empty:
                continue
            # 最新值取序列末点（按接口返回顺序，通常最近在后）
            pe_latest = float(pe_series.iloc[-1])
            pb_latest = float(pb_series.iloc[-1])
            # 五年分位：当前值在历史序列中的百分位排名（0~100）
            pe_pct = float((pe_series <= pe_latest).mean() * 100)
            pb_pct = float((pb_series <= pb_latest).mean() * 100)
            result[code] = {
                "pe_pct": round(pe_pct, 1),
                "pb_pct": round(pb_pct, 1),
            }
            logger.info(f"估值 {code}: PE分位={pe_pct:.0f}% PB分位={pb_pct:.0f}%")
        except Exception as exc:  # noqa: BLE001 - akshare 网络异常不可控
            logger.warning(f"估值 {code} 获取失败：{exc}")
            continue
    logger.info(f"估值分位获取完成：{len(result)}/{len(codes)} 只")
    return result


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
