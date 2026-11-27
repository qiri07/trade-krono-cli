#!/usr/bin/env python3
"""通过今日流程汇集（多线共振视角）分析单只股票，并推送飞书。

流程：
  1. 从各选股线结果文件收集代码集合（collect_source_sets）
  2. 计算目标股票命中了哪些选股线（多线共振视角）
  3. 对目标股票做技术分析（MA/趋势/量能/综合分 + PE/PB 估值）
  4. AI 核实（Nemotron，多线共振视角摘要）
  5. 构建飞书卡片（汇集命中来源 + 技术面 + 基本面 + AI 核实）并推送

用法：
  uv run python scripts/analyze_ticker.py sh.600019
  uv run python scripts/analyze_ticker.py 600019                # 裸代码自动加前缀
  uv run python scripts/analyze_ticker.py sh.600019 --no-push   # 只分析不推送
  uv run python scripts/analyze_ticker.py sh.600019 --date 2026-11-26
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger

from scripts._utils import expected_trading_day, normalize_ticker_key
from scripts.combined_stock_collect import _SOURCE_LABELS, collect_source_sets
from scripts.daily_analysis import ai_verify
from scripts.daily_analysis.card import _build_stock_card
from scripts.daily_analysis.core import analyze_stock
from scripts.feishu_utils import push_feishu
from trade_krono_cli.cli_commands.core import _load_env


def _resolve_ticker(code: str) -> str:
    """将裸代码或带前缀代码归一化为带前缀 ticker（sh./sz./bj.）。"""
    return normalize_ticker_key(code)


def _hit_sources(ticker: str, source_sets: dict[str, set[str]]) -> tuple[list[str], int]:
    """计算目标股票命中了哪些选股线（多线共振视角）。

    返回 (命中的来源键列表, 命中条数)。
    """
    code6 = ticker.split(".")[-1]
    hits: list[str] = []
    for key, codes in source_sets.items():
        # 各选股线结果文件统一以 6 位裸代码存储
        if code6 in codes:
            hits.append(key)
    return hits, len(hits)


def _build_focused_card(
    ticker: str,
    result: dict,
    hits: list[str],
    hit_count: int,
    source_sets: dict[str, set[str]],
    ai_note: str,
    date_str: str,
) -> str:
    """构建单只股票的聚焦卡片：汇集命中来源 + 技术面 + 基本面 + AI 核实。

    技术面卡片复用 daily_analysis.card._build_stock_card；
    汇集命中来源与 AI 核实作为附加段落拼接到卡片末尾。
    """
    base_card = _build_stock_card(result, date_str)

    # ── 汇集命中来源（多线共振视角）────────────────────────────────
    label_lines: list[str] = []
    if hit_count > 0:
        labels = " + ".join(_SOURCE_LABELS.get(h, h) for h in hits)
        label_lines.append(
            f"🔗 汇集命中：{labels}（{hit_count} 条选股线共振）"
        )
    else:
        label_lines.append("🔗 汇集命中：未命中当日任意选股线（单独聚焦分析）")

    # 各选股线当日命中数量概览（仅展示有命中的线）
    line_counts = [
        f"{_SOURCE_LABELS.get(k, k)} {len(v)}只"
        for k, v in source_sets.items()
        if v
    ]
    if line_counts:
        label_lines.append("各选股线命中数量：" + " · ".join(line_counts))

    # ── AI 核实 ─────────────────────────────────────────────────
    ai_lines: list[str] = []
    if ai_note:
        ai_lines.append("")
        ai_lines.append("── AI 核实 ──")
        ai_lines.append(ai_note)

    extra = "\n".join(label_lines + ai_lines)
    return f"{base_card}\n{extra}"


def main() -> None:
    """主流程：汇集命中 + 技术分析 + AI 核实 + 飞书推送。"""
    parser = argparse.ArgumentParser(
        description="通过今日流程汇集分析单只股票 + 飞书推送"
    )
    parser.add_argument("ticker", help="股票代码（sh.600019 / 600019 均可）")
    parser.add_argument(
        "--date",
        default=expected_trading_day(),
        help="分析日期 YYYY-MM-DD（默认最近交易日）",
    )
    parser.add_argument(
        "--push",
        type=int,
        default=1,
        help="是否飞书推送（1=是，0=否，默认 1）",
    )
    parser.add_argument(
        "--no-ai",
        type=int,
        default=0,
        help="跳过 AI 核实（1=跳过，0=执行，默认 0）",
    )
    args = parser.parse_args()

    _load_env()

    ticker = _resolve_ticker(args.ticker)
    logger.info("=" * 60)
    logger.info(f"📌 今日流程汇集分析  股票={ticker}  日期={args.date}")
    logger.info("=" * 60)

    # ── Step 1: 汇集各选股线代码集合 ─────────────────────────────
    source_sets = collect_source_sets(args.date)
    logger.info(
        "各选股线命中数量：" + " ".join(f"{k}={len(v)}" for k, v in source_sets.items())
    )

    # ── Step 2: 计算目标股票命中的选股线 ──────────────────────────
    hits, hit_count = _hit_sources(ticker, source_sets)
    logger.info(
        f"🔗 {ticker} 命中 {hit_count} 条选股线: {'+'.join(hits) if hits else '(无)'}"
    )

    # ── Step 3: 技术分析（MA/趋势/量能/综合分 + PE/PB 估值）──────
    logger.info(f"📈 对 {ticker} 做技术分析...")
    result = analyze_stock(ticker, end_date=args.date)
    logger.info(
        f"  {ticker} {result.get('name', '')}: 分数={result.get('score')} "
        f"趋势={result.get('trend')} 均线={result.get('ma_signal')}"
    )

    # ── Step 4: AI 核实（多线共振视角，单只）─────────────────────
    ai_note = ""
    if not args.no_ai:
        logger.info("🤖 启动 AI 核实...")
        try:
            # 单只 LLM 视图：技术分析结果 + 汇集命中来源
            llm_view = {
                "ticker": ticker,
                "name": result.get("name", ""),
                "trend": result.get("trend", "?"),
                "ma_signal": result.get("ma_signal", "?"),
                "vol_signal": result.get("vol_signal", "?"),
                "score": result.get("score", 0),
                "hit_sources": hits,
            }
            ai_result = ai_verify([llm_view], args.date)
            ai_note = (
                f"分析摘要：{ai_result.get('analysis_summary', '')}\n"
                f"风险提示：{ai_result.get('risk_alerts', '')}\n"
                f"结论：{ai_result.get('conclusion', '')}"
            )
            if ai_result.get("analysis_summary", "").startswith("LLM"):
                logger.info("AI 核实跳过：LLM 未配置")
                ai_note = ""
            else:
                logger.info(f"AI 核实完成：{ai_result.get('analysis_summary', '')[:80]}")
        except Exception as e:
            logger.warning(f"AI 核实失败（不影响推送）: {e}")
            ai_note = ""

    # ── Step 5: 构建飞书卡片 ────────────────────────────────────
    card = _build_focused_card(
        ticker, result, hits, hit_count, source_sets, ai_note, args.date
    )
    logger.info("\n" + "─" * 30 + "\n" + card + "\n" + "─" * 30)

    # ── Step 6: 飞书推送 ────────────────────────────────────────
    if args.push:
        title = f"📌 {args.date} 汇集分析 {ticker} {result.get('name', '')}"
        ok = push_feishu("text", content=card, title=title)
        logger.info(f"{'✅' if ok else '❌'} 飞书推送{'成功' if ok else '失败'}")
    else:
        logger.info("已跳过飞书推送（--push 0）")

    logger.info("=" * 60)
    logger.info(f"汇集分析完成：{ticker} 命中 {hit_count} 条线，技术分={result.get('score')}")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
