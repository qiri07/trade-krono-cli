#!/usr/bin/env python3
"""港股每日筛选全流程脚本。

流程：数据获取（本地 K 线 + 本地元数据）→ 交易规则硬过滤 → 流动性/市值过滤
→ 财务基本面排雷 → 庄股/异常过滤 → 质量加分排序 → JSON/CSV 落盘 → 飞书推送。

用法：
    uv run python scripts/hk_daily_screen.py                       # 全量筛选（最近交易日）
    uv run python scripts/hk_daily_screen.py --date 2026-11-23      # 指定分析日期
    uv run python scripts/hk_daily_screen.py --top 20               # 只输出前 20 名
    uv run python scripts/hk_daily_screen.py --min-pass-rate 0.9    # 允许 10% 规则未通过
    uv run python scripts/hk_daily_screen.py --min-coverage 0.7     # 数据完整度门槛
    uv run python scripts/hk_daily_screen.py --codes "00700,00941"  # 指定股票
    uv run python scripts/hk_daily_screen.py --no-push              # 不推送飞书
    uv run python scripts/hk_daily_screen.py --require-hk-connect   # 强制港股通资格
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any  # JSON 序列化内容值类型混杂，Any 为合理

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
from loguru import logger

from scripts._utils import expected_trading_day
from scripts.hk_screen import (
    HKScreenResult,
    compute_quality_score,
    fetch_hk_raw_data,
    get_hk_connect_stocks,
    get_hk_stock_list_from_akshare,
    get_hk_stock_list_from_cache,
    print_screen_summary,
    raw_to_screen_result,
    screen_hk_stocks,
)
from scripts.hk_screen.constants import HK_MIN_RULE_COVERAGE, OUTPUT_COLUMNS
from scripts.hk_screen.screener import fmt_cap, fmt_pe, fmt_roe
from trade_krono_cli.cli_commands.core import _load_env
from trade_krono_cli.config import get_settings


def _build_parser() -> argparse.ArgumentParser:
    """构造命令行解析器。"""
    parser = argparse.ArgumentParser(description="港股每日筛选全流程")
    parser.add_argument("--top", type=int, default=0, help="只输出前 N 名（0=全部）")
    parser.add_argument(
        "--min-pass-rate",
        type=float,
        default=1.0,
        help="已评估规则的最小通过比例（1.0=全通过）",
    )
    parser.add_argument(
        "--min-coverage",
        type=float,
        default=HK_MIN_RULE_COVERAGE,
        help=f"数据完整度门槛（默认 {HK_MIN_RULE_COVERAGE}，低于此值判不通过）",
    )
    parser.add_argument("--output", type=str, default=None, help="额外输出 CSV 路径")
    parser.add_argument("--json", type=str, default=None, help="JSON 输出路径（默认 results 目录）")
    parser.add_argument("--no-push", action="store_true", help="不推送飞书")
    parser.add_argument(
        "--require-hk-connect",
        action="store_true",
        help="强制要求港股通资格（默认不强制）",
    )
    parser.add_argument("--date", type=str, default=None, help="分析日期 YYYY-MM-DD")
    parser.add_argument("--codes", type=str, default=None, help="指定代码，逗号分隔")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 只（0=全部）")
    parser.add_argument(
        "--no-live-fallback",
        action="store_true",
        help="本地数据缺失时不回退 akshare 实时快照（离线复算用）",
    )
    return parser


def _resolve_tickers(args: argparse.Namespace) -> list[str]:
    """确定待筛选的港股列表（--codes 优先，其次本地缓存，最后 akshare）。"""
    if args.codes:
        codes = [c.strip().replace("hk.", "") for c in args.codes.split(",") if c.strip()]
        return [f"hk.{c.zfill(5)}" for c in codes]

    tickers = get_hk_stock_list_from_cache()
    if not tickers:
        logger.warning("⚠️  本地缓存无港股数据，尝试从 akshare 获取全量列表")
        tickers = get_hk_stock_list_from_akshare()
    if args.limit > 0:
        tickers = tickers[: args.limit]
    return tickers


def _write_json(path: Path, results: list[HKScreenResult], analysis_date: str) -> None:
    """把全量筛选结果写入 JSON（供下游/复盘读取）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    passed = [r for r in results if r.passed]
    payload: dict[str, Any] = {
        "date": analysis_date,
        "total": len(results),
        "passed": len(passed),
        "pass_rate": round(len(passed) / len(results), 4) if results else 0.0,
        "avg_coverage": (
            round(sum(r.coverage for r in results) / len(results), 4) if results else 0.0
        ),
        "results": [r.to_dict() for r in results],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"💾 结果已保存到: {path}（全量 {len(results)} 只 / 通过 {len(passed)} 只）")


def _summarize_failures(results: list[HKScreenResult], top: int = 8) -> list[str]:
    """统计最常见的失败原因（便于判断规则是否过严）。"""
    from collections import Counter

    counter: Counter[str] = Counter()
    for r in results:
        for reason in r.fail_reasons:
            counter[reason] += 1
    return [f"{reason}: {count} 只" for reason, count in counter.most_common(top)]


def _push_feishu(
    results: list[HKScreenResult],
    analysis_date: str,
    min_coverage: float,
) -> None:
    """推送筛选摘要到飞书。"""
    try:
        from scripts.feishu_utils import push_feishu
    except ImportError as e:
        logger.warning(f"⚠️ 飞书模块不可用: {e}")
        return

    passed = [r for r in results if r.passed]
    lines = [
        f"📈 **港股筛选结果 {analysis_date}**",
        f"候选 {len(results)} 只 → 通过 {len(passed)} 只",
        f"数据完整度门槛 {min_coverage:.0%}\n",
    ]
    for r in passed[:20]:
        lines.append(
            f"  {r.ticker} {(r.name or '未知')[:10]:<10} "
            f"市值={fmt_cap(r.market_cap)} "
            f"PE={fmt_pe(r.pe_ttm)} "
            f"ROE={fmt_roe(r.avg_roe_3y)} "
            f"质量分={compute_quality_score(r):.0f} "
            f"规则={r.pass_count}/{r.total_rules}"
        )

    failures = _summarize_failures([r for r in results if not r.passed])
    if failures:
        lines.append("\n**主要淘汰原因**")
        lines.extend(f"  · {item}" for item in failures)

    try:
        push_feishu(
            "text",
            content="\n".join(lines),
            title=f"港股筛选结果 {analysis_date}",
        )
        logger.info("✅ 飞书推送成功")
    except Exception as e:  # 推送失败不应阻断筛选流程
        logger.warning(f"⚠️ 飞书推送失败: {e}")


def main(argv: list[str] | None = None) -> int:
    """执行港股每日筛选流程。

    Args:
        argv: 命令行参数（``None`` 使用 ``sys.argv``）。

    Returns:
        进程退出码（0 成功，1 无数据）。
    """
    args = _build_parser().parse_args(argv)
    _load_env()

    analysis_date = args.date or expected_trading_day()
    logger.info(f"📅 港股筛选分析日期: {analysis_date}")

    tickers = _resolve_tickers(args)
    logger.info(f"📋 待筛选港股数量: {len(tickers)} 只")
    if not tickers:
        logger.error("❌ 没有可筛选的港股（缓存与接口均为空）")
        return 1

    hk_connect_stocks = None if args.codes else get_hk_connect_stocks()

    raw_data = fetch_hk_raw_data(
        tickers=tickers,
        hk_connect_stocks=hk_connect_stocks,
        date=analysis_date,
        live_fallback=not args.no_live_fallback,
    )
    results = [raw_to_screen_result(r) for r in raw_data]

    passed = screen_hk_stocks(
        results,
        min_pass_rate=args.min_pass_rate,
        require_hk_connect=args.require_hk_connect,
        min_coverage=args.min_coverage,
    )
    print_screen_summary(results)

    top_results = passed[: args.top] if args.top > 0 else passed
    logger.info("\n📋 筛选通过股票明细:")
    for i, r in enumerate(top_results, 1):
        logger.info(
            f"  {i:3d}. {r.ticker} {r.name:<20} "
            f"市值={fmt_cap(r.market_cap)} "
            f"PE={fmt_pe(r.pe_ttm)} "
            f"ROE={fmt_roe(r.avg_roe_3y)} "
            f"质量分={compute_quality_score(r):.0f} "
            f"通过规则={r.pass_count}/{r.total_rules} "
            f"未知={r.unknown_count}"
        )

    # JSON 固定落盘（默认 results 目录，供下游/复盘读取）
    if args.json:
        json_path = Path(args.json)
    else:
        results_dir = Path(get_settings().results_dir)
        json_path = results_dir / f"hk_screen_{analysis_date.replace('-', '')}.json"
    _write_json(json_path, results, analysis_date)

    if args.output:
        df = pd.DataFrame([r.to_dict() for r in passed])
        df = df[[c for c in OUTPUT_COLUMNS if c in df.columns]]
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(args.output, index=False, encoding="utf-8-sig")
        logger.info(f"💾 CSV 已保存到: {args.output}（{len(passed)} 只）")

    if not args.no_push:
        _push_feishu(results, analysis_date, args.min_coverage)

    logger.info("✅ 港股筛选完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
