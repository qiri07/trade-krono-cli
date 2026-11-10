#!/usr/bin/env python3
"""综合选股汇总结算：交集股票分析 + AI 核实 + 飞书推送（汇总 + 逐只）。

对当日筛选流程中「多条选股线共有的股票」做交集汇总：
  ① 白名单/巴菲特（合并为单一来源）  ② 三线共振(MACD)  ③ 多头排列  ④ 双均线金叉

命中 ≥2 条选股线的股票即进入交集候选池，对每只候选：
  - 标注命中的选股线来源
  - 做基础技术分析（MA 排列 / 趋势 / 量能 / 综合分）
  - 做基本面评估（PE/PB/估值分/估值结论）
  - 按命中条数加权排序（共振越多越靠前）

可选增强（推送前填充，写入每只卡片）：
  - --kronos 1：批量跑 Kronos K 线预测（首次加载模型约 1-3 分钟）
  - --backtest 1：逐只读取 research DB 历史信号做单股信号回测

随后 AI 核实 + 飞书推送（汇总卡片 + 逐只卡片）。

用法：
  uv run python scripts/combined_stock_analysis.py
  uv run python scripts/combined_stock_analysis.py --date 2026-10-15 --min-sources 2
  uv run python scripts/combined_stock_analysis.py --push 0   # 跳过飞书推送
  uv run python scripts/combined_stock_analysis.py --per-stock-push 0  # 跳过逐只推送
  uv run python scripts/combined_stock_analysis.py --kronos 1 --backtest 1  # 卡片含预测+回测
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger

from scripts.combined_stock_analyze import analyze_candidates, attach_backtest, attach_kronos
from scripts.combined_stock_cards import (
    _build_combined_card,
    _llm_view,
    _push_per_stock,
)
from scripts.combined_stock_collect import collect_source_sets
from scripts.combined_stock_intersect import build_intersection
from scripts.daily_analysis import ai_verify
from scripts.feishu_utils import push_feishu
from trade_krono_cli.cli_commands.core import _load_env
from trade_krono_cli.research_db import get_research  # noqa: PLC0415

# ─────────────────────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────────────────────


def main() -> None:
    """主流程：汇总交集 → 技术分析 → AI 核实 → 飞书推送。"""
    parser = argparse.ArgumentParser(description="综合选股交集分析 + 飞书推送")
    parser.add_argument(
        "--date",
        default=datetime.now().strftime("%Y-%m-%d"),
        help="分析日期 YYYY-MM-DD（默认今日）",
    )
    parser.add_argument(
        "--min-sources",
        type=int,
        default=3,
        help="最少命中几条选股线才进入交集池（默认 3）",
    )
    parser.add_argument(
        "--sort-by",
        choices=("hits", "score"),
        default="hits",
        help="结果排序：hits=按交集条数降序（默认），score=命中数*10+技术分",
    )
    parser.add_argument(
        "--max-analyze",
        type=int,
        default=60,
        help="最多对前 N 只做技术分析（默认 60）",
    )
    parser.add_argument(
        "--push",
        type=int,
        default=1,
        help="是否飞书推送（1=是，0=否，默认 1）",
    )
    parser.add_argument(
        "--per-stock-push",
        type=int,
        default=1,
        help="是否逐只推送飞书（1=是，0=否，默认 1；需 --push=1 同时开启）",
    )
    parser.add_argument(
        "--kronos",
        type=int,
        default=0,
        help="逐只推送前对候选跑 Kronos 预测（1=是，0=否，默认 0；首次加载模型约 1-3 分钟）",
    )
    parser.add_argument(
        "--backtest",
        type=int,
        default=0,
        help="逐只推送前对候选做该股历史信号回测（1=是，0=否，默认 0）",
    )
    parser.add_argument(
        "--backtest-days",
        type=int,
        default=90,
        help="回测信号回溯窗口（天，默认 90）",
    )
    parser.add_argument(
        "--backtest-horizon",
        type=int,
        default=5,
        help="回测持仓周期（交易日，默认 5）",
    )
    args = parser.parse_args()

    _load_env()

    logger.info("=" * 60)
    logger.info(
        f"🔗 综合选股交集分析启动  日期={args.date}  min_sources={args.min_sources}"
    )
    logger.info("=" * 60)

    # ── Step 1: 汇总各选股线代码集合 ─────────────────────────────
    source_sets = collect_source_sets(args.date)
    logger.info(
        "各选股线命中数量：" + " ".join(f"{k}={len(v)}" for k, v in source_sets.items())
    )

    # ── Step 0.5: 前置校验（P2：确保依赖步骤的输出文件存在且非空）─────────
    # 仅检查 source_sets 中实际存在的来源键；若某来源集合为空，检查对应文件是否存在。
    # 未出现在 source_sets 中的来源（如测试中未 mock 的）不做校验，避免误报。
    dc = args.date.replace("-", "")
    dd = args.date
    _source_file_map: dict[str, Path] = {
        "buffett": Path(f"outputs/results/buffett_screen_{dc}.txt"),
        "whitelist": Path("outputs/results/buffett_dynamic_whitelist.txt"),
        "resonance": Path(f"outputs/results/macd_resonance_{dd}.json"),
        "multi_head": Path(f"outputs/results/multi_head_{dc}.txt"),
        "double_ma": Path(f"outputs/results/double_ma_golden_cross_{dd}.json"),
        "macd_ma": Path(f"outputs/results/macd_ma_screener_{dc}.json"),
    }
    _czsc_dir = Path("outputs/czsc_signal_scan")
    _czsc_files = [
        (_czsc_dir / f"matched_bi_ma_{dc}.csv", "czsc_bi_ma"),
        (_czsc_dir / f"matched_third_buy_{dc}.csv", "czsc_third_buy"),
    ]
    _missing: list[str] = []
    for src, path in _source_file_map.items():
        codes = source_sets.get(src)
        if codes is not None and not codes and (not path.exists() or path.stat().st_size == 0):
            _missing.append(f"{src}: {path}")
    for path, src_key in _czsc_files:
        codes = source_sets.get(src_key)
        if codes is not None and not codes and (not path.exists() or path.stat().st_size == 0):
            _missing.append(f"czsc: {path}")
    if _missing:
        logger.error("❌ 综合选股前置校验失败，以下依赖文件缺失或为空：")
        for m in _missing:
            logger.error(f"   - {m}")
        logger.error("   请先运行相应选股步骤（buffett/resonance/multi_head/double_ma/macd_ma/czsc）")
        sys.exit(1)
    logger.info("✅ 前置校验通过：所有依赖选股线输出文件存在且非空")

    # ── Step 2: 交集候选池（≥N 条线）────────────────────────────
    cands = build_intersection(source_sets, args.min_sources)
    logger.info(
        f"命中 ≥{args.min_sources} 条选股线的股票：{len(cands)} 只"
        f"（按共振度排序，前 {min(len(cands), 20)} 只如下）"
    )
    for c in cands[:20]:
        logger.info(f"  🔗 {c.code}  命中 {c.hit_count} 条: {'+'.join(c.sources)}")

    # ── Step 3: 对候选做技术分析 ────────────────────────────────
    logger.info(f"\n📈 对前 {args.max_analyze} 只候选做技术分析...")
    analyzed = analyze_candidates(cands, args.date, args.max_analyze)
    if args.sort_by == "hits":
        # 交集条数优先（同条数再按技术分），满足「共振越多越靠前」
        analyzed.sort(key=lambda c: (-c.hit_count, -c.rank_score))
    logger.info(f"  有效技术分析结果：{len(analyzed)} 只")

    # ── Step 4: 汇总文件路径（在 Step 5.6 回测 attach 之后写出，确保 kronos/backtest 完整）──
    dc = args.date.replace("-", "")
    out_file = Path(f"outputs/results/combined_intersection_{dc}.json")
    out_file.parent.mkdir(parents=True, exist_ok=True)

    def _write_intersection_json() -> None:
        """构造并写出交集汇总 JSON（kronos / backtest 字段在此时已填充）。"""
        out_payload = {
            "date": args.date,
            "min_sources": args.min_sources,
            "source_counts": {k: len(v) for k, v in source_sets.items()},
            "intersection_total": len(cands),
            "analyzed": [
                {
                    "code": c.code,
                    "hit_count": c.hit_count,
                    "sources": c.sources,
                    **c.tech.to_payload(),
                    **c.fund.to_payload(),
                    "kronos": (c.kronos_view.to_payload() if c.kronos is not None else None),
                    "backtest": (
                        c.backtest.to_payload() if c.backtest is not None else None
                    ),
                }
                for c in analyzed
            ],
        }
        out_file.write_text(
            json.dumps(out_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(f"💾 结果已保存: {out_file}")

    # ── Step 5: AI 核实（聚合交集 + 多线共振视角）────────────────
    logger.info("🤖 启动 AI 核实...")
    ai_summary = ""
    try:
        ai_results_for_llm = [_llm_view(c) for c in analyzed]
        ai_result = ai_verify(ai_results_for_llm, args.date)
        ai_summary = (
            f"分析摘要：{ai_result.get('analysis_summary', '')}\n"
            f"Top 推荐：{ai_result.get('top_picks', '')}\n"
            f"风险提示：{ai_result.get('risk_alerts', '')}\n"
            f"结论：{ai_result.get('conclusion', '')}"
        )
        if ai_result.get("analysis_summary", "").startswith("LLM"):
            logger.info("AI 核实跳过：LLM 未配置")
        else:
            logger.info(
                f"AI 核实完成：{ai_result.get('analysis_summary', '')[:80]}"
            )
    except Exception as e:
        logger.warning(f"AI 核实失败（不影响推送）: {e}")
        ai_summary = ""

    # ── Step 5.5: Kronos 预测（可选，推送前填充，写入卡片与 LLM 视图）─
    if args.kronos:
        logger.info("🧠 启动 Kronos 预测...")
        attach_kronos(analyzed, args.date)

    # ── Step 5.6: 历史回测（可选，逐只信号回测，写入卡片）───────
    if args.backtest:
        logger.info(
            f"📈 启动历史回测（窗口 {args.backtest_days} 天，"
            f"horizon {args.backtest_horizon} 日）..."
        )
        attach_backtest(analyzed, args.date, args.backtest_days, args.backtest_horizon)

    # ── Step 5.7: 写出汇总 JSON（含 kronos / backtest 字段）────
    _write_intersection_json()

    # ── Step 6: 飞书推送（汇总 + 逐只）───────────────────────────
    if args.push:
        # 6a. 汇总卡片推送
        card = _build_combined_card(analyzed, args.date, source_sets)
        if ai_summary:
            card += f"\n\n---\n🤖 **AI 核实（多线共振视角）**\n{ai_summary}"
        ok = push_feishu("text", content=card, title=f"🔗 {args.date} 多线共振综合选股")
        logger.info(
            f"{'✅' if ok else '❌'} 飞书汇总推送{'成功' if ok else '失败'}"
        )

        # 6b. 逐只推送（每只单独一张卡片，含技术面+基本面）
        if args.per_stock_push and analyzed:
            _push_per_stock(analyzed, args.date)
    else:
        logger.info("已跳过飞书推送（--push 0）")

    # ── Step 7: 写入研究数据库（jobs + signals 表），供回测等后续步骤读取 ──
    try:
        research = get_research()
        job_id = research.create_job(
            date=args.date,
            tickers=[c.code for c in analyzed],
            settings=None,
            notes=f"combined_stock_analysis min_sources={args.min_sources}, analyzed={len(analyzed)}",
        )
        research.insert_signals(
            job_id=job_id,
            merged_items=[
                {
                    "ticker": c.code,
                    "rank": idx + 1,
                    "composite_score": c.hit_count,
                    "ranking_score": c.tech.score if c.tech else 0,
                    "ta_signal": c.tech.ma_signal if c.tech else None,
                    "ta_confidence": c.tech.score if c.tech else None,
                    "ta_reasoning": (
                        (c.tech.ma_signal or "")[:200] if c.tech else ""
                    ),
                    "kronos_direction": c.kronos.direction if c.kronos else None,
                    "kronos_change_pct": c.kronos.expected_change_pct
                    if c.kronos and c.kronos.expected_change_pct is not None
                    else None,
                    "kronos_prediction_uncertainty": (
                        c.kronos.prediction_uncertainty if c.kronos else None
                    ),
                    "ta_error": None,
                    "kronos_error": None,
                    "signal_assessment_json": json.dumps(
                        {
                            "command_score": c.tech.score if c.tech else 0,
                            "sources": c.sources,
                            "hit_count": c.hit_count,
                        },
                        ensure_ascii=False,
                    ),
                    "expected_value": None,
                    "conflict": "",
                }
                for idx, c in enumerate(analyzed)
            ],
        )
        logger.info(
            f"📊 已写入 research_db：job_id={job_id}，{len(analyzed)} 条信号"
        )
    except Exception as e:
        logger.warning(f"写入 research_db 失败（不影响结果输出）：{e}")

    logger.info("=" * 60)
    per_push_info = (
        f"，逐只推送 {len(analyzed)} 只"
        if (args.push and args.per_stock_push and analyzed)
        else ""
    )
    logger.info(
        f"综合选股交集分析完成：交集 {len(cands)} 只，技术分析 {len(analyzed)} 只{per_push_info}"
    )
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
