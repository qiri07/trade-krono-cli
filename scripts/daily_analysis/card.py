#!/usr/bin/env python3
"""daily_analysis 飞书卡片构建模块。"""

from __future__ import annotations

from typing import Any


def _build_stock_card(r: dict[str, Any], date_str: str) -> str:
    """构建单只股票的飞书消息文本。"""
    is_exrights = r.get("exrights", False)
    emoji = "🟢" if r["score"] >= 65 else ("🟡" if r["score"] >= 45 else "🔴")
    action = "BUY" if r["score"] >= 65 else ("HOLD" if r["score"] >= 45 else "SELL")
    buy = "、".join(r.get("buy_points", [])) or "暂无"
    sell = "、".join(r.get("sell_points", [])) or "暂无"
    exrights_tag = " 📌除权日" if is_exrights else ""
    change_str = f"{r.get('change', 0):+.2f}%" if not is_exrights else "除权调整"

    # ── 估值信息 ─────────────────────────────────────────────────────
    val_score = r.get("valuation_score")
    buy_price = r.get("buy_price_suggested")
    pe_ttm = r.get("pe_ttm")
    pb = r.get("pb")
    valuation_conc = r.get("valuation_conclusion", "")

    val_lines: list[str] = []
    if pe_ttm is not None:
        val_lines.append(f"PE_TTM={pe_ttm:.1f}")
    if pb is not None:
        val_lines.append(f"PB={pb:.2f}")
    if val_score is not None:
        v_emoji = "🟢" if val_score >= 70 else ("🟡" if val_score >= 50 else "🔴")
        val_lines.append(f"估值分={v_emoji}{val_score:.0f}")
    if buy_price is not None:
        buy_tag = "↑" if buy_price > r.get("close", 0) else "↓"
        val_lines.append(f"建议买入价={buy_price:.2f}{buy_tag}")
    val_section = "\n".join(f"  {line}" for line in val_lines) if val_lines else ""
    conc_section = f"\n  {valuation_conc}" if valuation_conc else ""

    return (
        f"**📊 {date_str} · {r['ticker']} {r['name']}{exrights_tag}**\n"
        f"{emoji} **综合分：{r['score']}**（{action}）\n"
        f"收盘价：{r.get('close', '?')}  今日涨跌：{change_str}\n"
        f"趋势：{r['trend']}　均线信号：{r['ma_signal']}　量能信号：{r['vol_signal']}\n"
        f"MA5={r.get('ma5', '?')}　MA10={r.get('ma10', '?')}　MA20={r.get('ma20', '?')}\n"
        f"20日区间：{r.get('low_20', '?')} ~ {r.get('high_20', '?')}\n"
        f"🟢 买点信号：{buy}\n"
        f"🔴 卖点信号：{sell}" + (f"\n{val_section}{conc_section}" if val_section else "")
    )
