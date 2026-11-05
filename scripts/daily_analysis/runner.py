#!/usr/bin/env python3
"""daily_analysis 主流程模块：白名单分析 + 飞书推送。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from loguru import logger

from scripts._utils import check_data_freshness
from scripts.daily_analysis.core import analyze_stock
from scripts.daily_analysis.whitelist import (
    _filter_bse_st,
    _get_merged_whitelist,
)
from scripts.feishu_utils import push_feishu
from trade_krono_cli.cli_commands._core_helpers import _load_env


def run_analysis(date: str, tickers_str: str) -> None:
    """执行白名单技术分析 + 汇总飞书推送。"""
    _load_env()

    # 前置：检查缓存数据新鲜度
    logger.info(f"[数据检查] {check_data_freshness(logger.info)}")

    # ── Step 1: 解析股票代码（过滤 ST / 北交所）─────────────────────────────
    raw_codes = [c.strip() for c in tickers_str.split(",") if c.strip()]
    tickers_with_prefix: list[str] = []
    for code in raw_codes:
        if "." in code:
            tickers_with_prefix.append(code)
        elif code.startswith("6"):
            tickers_with_prefix.append(f"sh.{code}")
        else:
            tickers_with_prefix.append(f"sz.{code}")

    tickers_with_prefix = _filter_bse_st(tickers_with_prefix)

    logger.info(f"📊 白名单分析：{tickers_with_prefix}  日期={date}")

    # ── Step 2: 逐一技术分析（不逐只推送，汇总后统一推送）────────────────
    results: list[dict[str, Any]] = []
    for ticker in tickers_with_prefix:
        result = analyze_stock(ticker, end_date=date)
        results.append(result)
        status_emoji = "✅" if result["status"] == "ok" else "⚠️"
        logger.info(
            f"  {status_emoji} {ticker} {result['name']}: "
            f"分数={result['score']} 趋势={result['trend']} 均线={result['ma_signal']}"
        )

    # ── Step 3: 汇总推送 ────────────────────────────────────────────────
    ok_results = [r for r in results if r["status"] == "ok"]
    no_data_results = [r for r in results if r["status"] != "ok"]
    buy_signals = [r for r in ok_results if r["score"] >= 65]
    hold_signals = [r for r in ok_results if 45 <= r["score"] < 65]
    sell_signals = [r for r in ok_results if r["score"] < 45]

    # 估值统计
    val_scores = [r["valuation_score"] for r in ok_results if r.get("valuation_score") is not None]
    buy_price_count = sum(1 for r in ok_results if r.get("buy_price_suggested") is not None)
    low_val_stocks = [
        r for r in ok_results if r.get("valuation_score") is not None and r["valuation_score"] >= 70
    ]

    top3_sorted = sorted(ok_results, key=lambda x: x["score"], reverse=True)[:3]
    top3_str = (
        "  ".join(
            f"{r['ticker']} {r['name']}:{'BUY' if r['score'] >= 65 else 'HOLD'} {r['score']}分"
            for r in top3_sorted
        )
        or "无"
    )

    summary_lines = [
        f"**📊 {date} 白名单分析汇总**\n",
        f"共 {len(ok_results)} 只有数据 · BUY {len(buy_signals)} 只 · HOLD {len(hold_signals)} 只 · SELL {len(sell_signals)} 只"
        f"{'，' + str(len(no_data_results)) + ' 只无数据' if no_data_results else ''}\n\n",
    ]
    if val_scores:
        avg_val = sum(val_scores) / len(val_scores)
        summary_lines.append(
            f"**📈 估值概览：** 平均估值分={avg_val:.0f}分，低估值股票（≥70分）{len(low_val_stocks)} 只，"
            f"有建议买入价 {buy_price_count} 只\n\n"
        )
    for r in sorted(ok_results, key=lambda x: x["score"], reverse=True):
        emoji = "🔴" if r["score"] >= 65 else ("🟡" if r["score"] >= 45 else "⚪")
        er_tag = " 📌除权日" if r.get("exrights", False) else ""
        change_str = f"{r['change']:+.2f}%" if not r.get("exrights") else "除权日"
        # 估值附加信息
        val_extra = ""
        if r.get("valuation_score") is not None:
            v_emoji = (
                "🟢"
                if r["valuation_score"] >= 70
                else ("🟡" if r["valuation_score"] >= 50 else "🔴")
            )
            vp = r.get("buy_price_suggested")
            vp_str = f"  建议买入价={vp:.2f}" if vp else ""
            val_extra = f"  估值{v_emoji}{r['valuation_score']:.0f}分{vp_str}"
        summary_lines.append(
            f"• {emoji} {r['ticker']} {r['name']}{er_tag}  分={r['score']}  趋势={r['trend']}  "
            f"均线={r['ma_signal']}  量能={r['vol_signal']}  今日={change_str}{val_extra}"
        )
    if no_data_results:
        summary_lines.append(f"\n⚠️ 无数据：{', '.join(r['ticker'] for r in no_data_results)}")

    summary_lines.append("\n")

    summary_content = "\n".join(summary_lines)

    ok = push_feishu(
        "daily",
        status="success",
        date=date,
        tickers=tickers_str,
        top3=top3_str,
        run_url="local",
        content=summary_content,
    )
    if ok:
        logger.info("✅ 汇总飞书推送成功")
    else:
        logger.error("❌ 汇总飞书推送失败")


def main() -> None:
    """命令行入口：解析参数并执行白名单分析 + 飞书推送。"""
    import argparse

    parser = argparse.ArgumentParser(description="白名单股票综合分析 + 飞书推送")
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"), help="分析日期")
    parser.add_argument(
        "--tickers",
        default=_get_merged_whitelist(),
        help="股票代码（逗号分隔，静态+动态白名单合并）",
    )
    args = parser.parse_args()

    run_analysis(date=args.date, tickers_str=args.tickers)


if __name__ == "__main__":
    main()
