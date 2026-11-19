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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger

from scripts._utils import expected_trading_day, load_metadata_index
from scripts.combined_stock_analyze import analyze_candidates, attach_backtest, attach_kronos
from scripts.combined_stock_cards import (
    _build_combined_card,
    _get_stock_name_from_metadata,
    _llm_view,
)
from scripts.combined_stock_collect import collect_source_sets
from scripts.combined_stock_intersect import build_intersection
from scripts.combined_stock_models import StockCandidate
from scripts.daily_analysis import ai_verify
from scripts.feishu_utils import push_feishu
from trade_krono_cli.cli_commands.core import _load_env
from trade_krono_cli.config import get_settings
from trade_krono_cli.research_db import get_research  # noqa: PLC0415

# ─────────────────────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────────────────────


def main() -> None:
    """主流程：汇总交集 → 技术分析 → AI 核实 → 飞书推送。"""
    parser = argparse.ArgumentParser(description="综合选股交集分析 + 飞书推送")
    parser.add_argument(
        "--date",
        default=expected_trading_day(),
        help="分析日期 YYYY-MM-DD（默认最近交易日）",
    )
    parser.add_argument(
        "--min-sources",
        type=int,
        default=3,
        help="最少命中几条选股线才进入交集池（默认 3）",
    )
    parser.add_argument(
        "--a-stock-screen",
        type=int,
        default=1,
        help="是否对交集候选做 A股 筛选硬过滤（1=是，0=否；默认 1，剔除 ST/北交所/ETF/停牌/低流动性/低价股）",
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
        default=0,
        help="是否逐只推送飞书（1=是，0=否；默认 0，只推汇总卡片）",
    )
    parser.add_argument(
        "--per-stock-limit",
        type=int,
        default=50,
        help="逐只推送最多前 N 只（默认 50；<=0 表示不限）",
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
    parser.add_argument(
        "--strict",
        type=int,
        default=0,
        help="严格模式：依赖选股线结果文件缺失/为空时直接失败（1=是，0=仅告警继续，默认 0）",
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
    results_dir = Path(get_settings().results_dir)
    czsc_dir = Path(get_settings().cache_dir).parent / "czsc_signal_scan"
    _source_file_map: dict[str, Path] = {
        "buffett": results_dir / f"buffett_screen_{dc}.txt",
        "whitelist": results_dir / "buffett_dynamic_whitelist.txt",
        "resonance": results_dir / f"macd_resonance_{dd}.json",
        "multi_head": results_dir / f"multi_head_{dc}.txt",
        "double_ma": results_dir / f"double_ma_golden_cross_{dd}.json",
        "macd_ma": results_dir / f"macd_ma_screener_{dc}.json",
        "six_dim": results_dir / f"six_dim_screen_{dc}.json",
    }
    _czsc_files = [
        (czsc_dir / f"matched_bi_ma_{dc}.csv", "czsc_bi_ma"),
        (czsc_dir / f"matched_third_buy_{dc}.csv", "czsc_third_buy"),
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
        if args.strict:
            logger.error("❌ 综合选股前置校验失败，以下依赖文件缺失或为空：")
            for m in _missing:
                logger.error(f"   - {m}")
            logger.error("   请先运行相应选股步骤（buffett/resonance/multi_head/double_ma/macd_ma/six_dim/czsc）")
            sys.exit(1)
        # 非严格模式：缺失线按 0 只参与交集，不阻断当日汇总（节假日/零命中日常见）
        logger.warning("⚠️ 以下选股线结果文件缺失或为空，将按 0 只参与交集：")
        for m in _missing:
            logger.warning(f"   - {m}")
    else:
        logger.info("✅ 前置校验通过：所有依赖选股线输出文件存在且非空")

    # ── Step 2: 交集候选池（≥N 条线）────────────────────────────
    cands = build_intersection(source_sets, args.min_sources)
    logger.info(
        f"命中 ≥{args.min_sources} 条选股线的股票：{len(cands)} 只"
        f"（按共振度排序，前 {min(len(cands), 20)} 只如下）"
    )
    for c in cands[:20]:
        logger.info(f"  🔗 {c.code}  命中 {c.hit_count} 条: {'+'.join(c.sources)}")

    # ── Step 2.5: A股筛选硬过滤（流动性/市值/停牌/股价/ST/北交所/ETF）──
    if args.a_stock_screen:
        logger.info("\n🧹 对交集候选做 A股 筛选硬过滤...")
        from scripts.a_stock_screen.screener import screen_a_stock_candidates

        meta = load_metadata_index()
        cand_codes = [c.code for c in cands]
        passed_codes, screen_details = screen_a_stock_candidates(
            cand_codes, args.date, metadata=meta
        )
        passed_set = set(passed_codes)

        filtered_out = [c for c in cands if c.code not in passed_set]
        cands = [c for c in cands if c.code in passed_set]

        logger.info(
            f"A股筛选：候选 {len(cand_codes)} 只，通过 {len(cands)} 只，"
            f"剔除 {len(filtered_out)} 只"
        )
        for c in filtered_out[:10]:
            detail = screen_details.get(c.code)
            reasons = detail.fail_reasons if detail else []
            cname = (meta.get(c.code) or {}).get("name", "")
            logger.info(
                f"  ❌ 剔除 {c.code} ({cname}): {'; '.join(reasons)}"
            )
    else:
        logger.info("\n⏭️  已跳过 A股 筛选硬过滤（--a-stock-screen 0）")

    # ── Step 3: 对候选做技术分析 ────────────────────────────────
    logger.info(f"\n📈 对前 {args.max_analyze} 只候选做技术分析...")
    analyzed = analyze_candidates(cands, args.date, args.max_analyze)
    if args.sort_by == "hits":
        # 交集条数优先（同条数再按技术分），满足「共振越多越靠前」
        analyzed.sort(key=lambda c: (-c.hit_count, -c.rank_score))
    else:
        # score：命中数*10 + 技术分（此前该分支实际未排序，参数形同虚设）
        analyzed.sort(key=lambda c: -(c.hit_count * 10 + c.rank_score))
    logger.info(f"  有效技术分析结果：{len(analyzed)} 只")

    # ── Step 4: 汇总文件路径（在 Step 5.6 回测 attach 之后写出，确保 kronos/backtest 完整）──
    dc = args.date.replace("-", "")
    out_file = Path(get_settings().results_dir) / f"combined_intersection_{dc}.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)

    def _candidate_entry(c: StockCandidate) -> dict[str, object]:
        """构造单只候选的 JSON 条目。

        已做技术分析的候选包含完整 tech/fund/kronos/backtest 字段；
        未做技术分析的候选（超出 max_analyze 范围）仅补 code/name/hit_count/sources。
        """
        entry: dict[str, object] = {
            "code": c.code,
            "hit_count": c.hit_count,
            "sources": list(c.sources),
        }
        if c.analysis is not None:
            entry.update(
                {
                    **c.tech.to_payload(),
                    **c.fund.to_payload(),
                    "kronos": c.kronos_view.to_payload() if c.kronos is not None else None,
                    "backtest": (
                        c.backtest.to_payload() if c.backtest is not None else None
                    ),
                }
            )
        else:
            # 未做技术分析：仅补名称，其余字段留空/默认
            entry["name"] = _get_stock_name_from_metadata(c.code) or ""
            entry["trend"] = ""
            entry["ma_signal"] = ""
            entry["vol_signal"] = ""
            entry["score"] = 0
            entry["close"] = None
            entry["ma5"] = None
            entry["pe_ttm"] = None
            entry["pb"] = None
            entry["valuation_score"] = None
            entry["valuation_conclusion"] = ""
            entry["kronos"] = None
            entry["backtest"] = None
        return entry

    def _write_intersection_json() -> None:
        """构造并写出交集汇总 JSON（所有候选均包含，未分析的补名称）。

        kronos / backtest 字段在 Step 5.5/5.6 之后已填充。
        """
        # 按交集次数降序排列，与飞书卡片展示顺序一致
        all_cands = sorted(cands, key=lambda c: -c.hit_count)
        out_payload = {
            "date": args.date,
            "min_sources": args.min_sources,
            "source_counts": {k: len(v) for k, v in source_sets.items()},
            "intersection_total": len(cands),
            "analyzed_count": len(analyzed),
            "analyzed": [_candidate_entry(c) for c in all_cands],
        }
        out_file.write_text(
            json.dumps(out_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(f"💾 结果已保存: {out_file} （{len(all_cands)} 只候选，其中 {len(analyzed)} 只已技术分析）")

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
        # 6a. 汇总卡片推送（cands 是全量 89 只，analyzed 只有前 60 只做了技术分析）
        card = _build_combined_card(
            cands,
            args.date,
            source_sets,
            total=len(cands),
            min_sources=args.min_sources,
            analyzed_map={c.code: c for c in analyzed},
        )
        if ai_summary:
            card += f"\n\n---\n🤖 **AI 核实（多线共振视角）**\n{ai_summary}"
        ok = push_feishu("text", content=card, title=f"🔗 {args.date} 多线共振综合选股")
        logger.info(
            f"{'✅' if ok else '❌'} 飞书汇总推送{'成功' if ok else '失败'}"
        )

        # 6b. 逐只推送（已禁用：仅推送汇总卡片用于验证）
        # if args.per_stock_push and analyzed:
        #     _push_per_stock(analyzed, args.date, limit=args.per_stock_limit)
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
    logger.info(
        f"综合选股交集分析完成：交集 {len(cands)} 只，技术分析 {len(analyzed)} 只"
        f"{'（仅汇总卡片）' if args.push else ''}"
    )
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
