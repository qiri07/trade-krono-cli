#!/usr/bin/env python3
"""A股筛选器 CLI 入口。

参考 scripts/hk_screen/cli.py，为 A股 提供独立的流动性、市值、价格、
停牌、ST、北交所、ETF 等硬过滤命令，可在今日流程之外直接使用。

过滤标准（参考港股筛选逻辑，改写为 A股 市场）：
  - 近 20 个交易日日均成交额 >= 5000 万元
  - 近 12 个月日均换手率 >= 0.1%
  - 总市值 >= 50 亿元
  - 剔除 ST / 北交所 / ETF / 过度停牌 / 低价股

用法：
    uv run python scripts/a_stock_screen/cli.py                              # 筛选全部缓存股票
    uv run python scripts/a_stock_screen/cli.py --codes "600519,000858"    # 指定股票
    uv run python scripts/a_stock_screen/cli.py --date 2026-11-20 --top 20 # 指定日期+前 20
    uv run python scripts/a_stock_screen/cli.py --output result.csv        # 保存 CSV
    uv run python scripts/a_stock_screen/cli.py --min-pass-rate 0.8        # 通过率>=80%
    uv run python scripts/a_stock_screen/cli.py --no-push                  # 不推送飞书
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from loguru import logger

from scripts._utils import expected_trading_day, get_all_tickers, load_metadata_index
from scripts.a_stock_screen import (
    AStockScreenResult,
    FiveMustResult,
    screen_a_stock_candidates,
    screen_five_must,
)
from scripts.universe_filter import apply_universe_filter

# ── 共享输出逻辑（原 scripts/a_stock_screen/_shared.py，已内联）──
OUTPUT_FIELDS: list[str] = [
    "code",
    "name",
    "close",
    "market_cap",
    "pe_ttm",
    "pb",
    "turnover_20d_avg",
    "turnover_rate_12m_avg",
    "listing_days",
    "suspension_days_30d",
    "is_st",
    "is_etf",
    "is_bse",
    "pass_count",
    "total_rules",
    "fail_reasons",
    "passed",
]

_OUTPUT_FIELDS = OUTPUT_FIELDS


def _fmt(val: Any, pct: bool = False) -> str:
    """安全格式化单元格值。"""
    if val is None:
        return "N/A"
    try:
        if pct:
            return f"{float(val):.1%}"
        if isinstance(val, float):
            return f"{val:,.2f}"
        return str(val)
    except (TypeError, ValueError):
        return "N/A"


def _build_row(r: AStockScreenResult) -> dict[str, str]:
    """把筛选结果转为展示用的字符串行。"""
    return {
        "code": r.code,
        "name": r.name,
        "close": _fmt(r.close),
        "market_cap": (
            f"{_fmt(r.market_cap / 1e8)}亿" if r.market_cap is not None else "N/A"
        ),
        "pe_ttm": _fmt(r.pe_ttm),
        "pb": _fmt(r.pb),
        "turnover_20d_avg": (
            f"{_fmt(r.turnover_20d_avg / 1e4)}万"
            if r.turnover_20d_avg is not None
            else "N/A"
        ),
        "turnover_rate_12m_avg": _fmt(r.turnover_rate_12m_avg, pct=True),
        "listing_days": str(r.listing_days) if r.listing_days is not None else "N/A",
        "suspension_days_30d": (
            str(r.suspension_days_30d) if r.suspension_days_30d is not None else "N/A"
        ),
        "is_st": "是" if r.is_st else "否",
        "is_etf": "是" if r.is_etf else "否",
        "is_bse": "是" if r.is_bse else "否",
        "pass_count": str(r.pass_count),
        "total_rules": str(r.total_rules),
        "fail_reasons": r.fail_reasons[0] if r.fail_reasons else "",
        "passed": "是" if r.passed else "否",
    }


def build_summary_lines(
    results: list[AStockScreenResult],
    passed: list[AStockScreenResult],
    filtered: list[AStockScreenResult],
    min_pass_rate: float,
) -> list[str]:
    """构建摘要文本行列表。"""
    return [
        "=" * 60,
        "A股筛选结果摘要",
        "=" * 60,
        f"候选股票总数: {len(results)} 只",
        f"通过所有规则: {len(passed)} 只",
        f"满足最小通过比例({min_pass_rate:.0%}): {len(filtered)} 只",
        "=" * 60,
    ]


def build_detail_lines(top_results: list[AStockScreenResult]) -> list[str]:
    """构建明细文本行列表。"""
    lines: list[str] = ["\n📋 筛选通过股票明细:"]
    for i, r in enumerate(top_results, 1):
        row = _build_row(r)
        passed_mark = "是" if r.passed else "否"
        lines.append(
            f"  {i:3d}. {row['code']} {row['name']:<12} "
            f"市值={row['market_cap']} "
            f"PE={row['pe_ttm']} "
            f"成交额20d={row['turnover_20d_avg']} "
            f"换手率12m={row['turnover_rate_12m_avg']} "
            f"规则={row['pass_count']}/{row['total_rules']} "
            f"通过={passed_mark}"
        )
        if r.fail_reasons:
            lines.append(f"         失败原因: {'; '.join(r.fail_reasons)}")
    return lines


def export_to_csv(filtered: list[AStockScreenResult], output_path: str) -> None:
    """把筛选结果保存为 CSV 文件。

    Parameters
    ----------
    filtered : list[AStockScreenResult]
        需要导出的筛选结果列表。
    output_path : str
        CSV 文件路径。
    """
    import pandas as pd

    df = pd.DataFrame([r.to_dict() for r in filtered])
    exist_cols = [c for c in OUTPUT_FIELDS if c in df.columns]
    df = df[exist_cols]
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False, encoding="utf-8-sig")


def push_to_feishu(
    passed: list[AStockScreenResult],
    top_results: list[AStockScreenResult],
    title: str = "A股筛选结果",
) -> bool:
    """通过飞书推送筛选结果。

    Parameters
    ----------
    passed : list[AStockScreenResult]
        通过所有规则的股票列表。
    top_results : list[AStockScreenResult]
        需要推送的明细列表（通常取前 20 名）。
    title : str
        飞书消息标题。

    Returns
    -------
    bool
        推送是否成功。
    """
    from scripts.feishu_utils import push_feishu  # noqa: PLC0415

    lines = [f"📈 **A股筛选结果**（共 {len(passed)} 只通过所有规则）\n"]
    for r in top_results[:20]:
        cap = f"{r.market_cap / 1e8:.0f}亿" if r.market_cap is not None else "N/A"
        pe = f"{r.pe_ttm:.1f}" if r.pe_ttm is not None else "N/A"
        turnover = (
            f"{r.turnover_20d_avg / 1e4:.0f}万"
            if r.turnover_20d_avg is not None
            else "N/A"
        )
        lines.append(
            f"  {r.code} {r.name[:10]:<10} "
            f"市值={cap} "
            f"PE={pe} "
            f"成交额20d={turnover} "
            f"规则={r.pass_count}/{r.total_rules}"
        )
    try:
        push_feishu("text", content="\n".join(lines), title=title)
        return True
    except Exception:
        return False


# 五必筛选结果展示字段
_FIVEMUST_FIELDS: list[str] = [
    "code",
    "name",
    "close",
    "price_percentile",
    "cond1_history_low",
    "cond1_reason",
    "volatility_20d",
    "return_5d",
    "cond2_bottom_solid",
    "cond2_reason",
    "roe",
    "roe_excl",
    "cond3_performance",
    "cond3_reason",
    "turnover_rate_20d",
    "share_cap_wan",
    "market_cap_yi",
    "cond4_concentration",
    "cond4_reason",
    "ma5",
    "ma13",
    "cond5_breakout",
    "cond5_reason",
    "pass_count",
    "total_rules",
    "fail_reasons",
    "passed",
]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="A股筛选器（流动性/市值/停牌/ST/北交所/ETF 硬过滤）"
    )
    parser.add_argument(
        "--codes",
        type=str,
        default=None,
        help="6 位裸代码，逗号分隔（如 600519,000858）",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="对缓存中的全部股票进行筛选",
    )
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="分析日期 YYYY-MM-DD（默认最近交易日）",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=0,
        help="只输出前 N 名（0=全部）",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="输出 CSV 文件路径（不指定则不保存）",
    )
    parser.add_argument(
        "--min-pass-rate",
        type=float,
        default=1.0,
        help="最小通过比例（1.0=必须通过所有规则，0.8=通过80百分比以上规则）",
    )
    parser.add_argument(
        "--no-push",
        action="store_true",
        help="不推送飞书",
    )
    parser.add_argument(
        "--fivemust",
        action="store_true",
        help="启用「五必」筛选（历史低位/底部扎实/业绩优秀/筹码集中/日K多头突破），代替常规硬过滤",
    )
    args = parser.parse_args()

    date_str = args.date or expected_trading_day()
    logger.info(f"🔍 A股筛选启动  日期={date_str}")

    # 确定待筛选代码
    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    elif args.all:
        codes = get_all_tickers()
    else:
        parser.error("必须指定 --codes 或 --all")

    logger.info(f"📋 待筛选股票数量: {len(codes)} 只")

    if args.fivemust:
        # 五必筛选仅作用于 A 股（排除港股/北交所/ETF/次新/停牌/低价/低流动性/小市值）
        a_codes = apply_universe_filter(codes, date_str, label="五必筛选")
        _run_fivemust(a_codes, date_str, args)
        return

    # ──────────────────────────────────────────────────────────
    # 常规硬过滤分支
    # ──────────────────────────────────────────────────────────
    # 统一使用前置股票池过滤（剔除 ST/北交所/ETF/次新/停牌/低价/低流动性/小市值）
    filtered_codes = apply_universe_filter(codes, date_str, label="A股筛选")

    # 加载元数据
    meta = load_metadata_index()

    # 执行筛选
    passed_codes, details = screen_a_stock_candidates(filtered_codes, date_str, metadata=meta)

    # 统计并排序全部结果（按 pass_count 降序、fail_reasons 长度升序）
    hard_results: list[AStockScreenResult] = list(details.values())
    hard_results.sort(key=lambda r: (-r.pass_count, len(r.fail_reasons)))

    # 按最小通过比例过滤
    min_rate = args.min_pass_rate
    filtered = [
        r
        for r in hard_results
        if r.total_rules == 0 or (r.pass_count / r.total_rules) >= min_rate
    ]

    passed = [r for r in filtered if r.passed]
    top_results = filtered[: args.top] if args.top > 0 else filtered

    # 打印摘要
    for line in build_summary_lines(hard_results, passed, filtered, min_rate):
        logger.info(line)

    if top_results:
        for line in build_detail_lines(top_results):
            logger.info(line)

    # 保存到 CSV
    if args.output:
        export_to_csv(filtered, args.output)
        logger.info(f"💾 结果已保存到: {args.output}（{len(filtered)} 只）")

    # 飞书推送（如果启用）
    if not args.no_push and passed:
        ok = push_to_feishu(passed, top_results, title="A股筛选结果")
        if ok:
            logger.info("✅ 飞书推送成功")
        else:
            logger.warning("⚠️ 飞书推送失败")

    logger.info("✅ A股筛选完成")


def _run_fivemust(codes: list[str], date_str: str, args: argparse.Namespace) -> None:
    """执行「五必」筛选并输出结果（打印 / CSV / 飞书）。"""
    # ──────────────────────────────────────────────────────────
    # 「五必」筛选分支
    # ──────────────────────────────────────────────────────────
    logger.info("🔍 五必筛选启动（历史低位/底部扎实/业绩优秀/筹码集中/日K多头突破）")
    passed_codes, fivemust_details = screen_five_must(codes, date_str)

    results: list[FiveMustResult] = list(fivemust_details.values())
    results.sort(key=lambda r: (-r.pass_count, len(r.fail_reasons)))
    passed = [r for r in results if r.passed]
    top_results = results[: args.top] if args.top > 0 else results

    # 始终写入固定文件名，供综合选股汇集（combined_stock_analysis）读取
    from trade_krono_cli.config import get_settings  # noqa: PLC0415

    _results_dir = Path(get_settings().results_dir)
    _dc = date_str.replace("-", "")
    default_output = _results_dir / f"fivemust_screen_{_dc}.csv"

    logger.info("=" * 60)
    logger.info("五必筛选结果摘要")
    logger.info("=" * 60)
    logger.info(f"候选股票总数: {len(results)} 只")
    logger.info(f"通过全部5条: {len(passed)} 只")
    logger.info("=" * 60)

    if top_results:
        logger.info("\n📋 五必筛选通过股票明细:")
        for i, r in enumerate(top_results, 1):
            cap = (
                f"{r.market_cap_yi:.0f}亿"
                if r.market_cap_yi is not None
                else "N/A"
            )
            ma5_str = f"{r.ma5:.2f}" if r.ma5 is not None else "N/A"
            logger.info(
                f"  {i:3d}. {r.code} {r.name:<12} "
                f"收盘={r.close} "
                f"市值={cap} "
                f"分位={r.price_percentile} "
                f"ROE={r.roe} "
                f"换手率={r.turnover_rate_20d} "
                f"MA5={ma5_str} "
                f"规则={r.pass_count}/{r.total_rules} "
                f"通过={'是' if r.passed else '否'}"
            )
            if r.fail_reasons:
                logger.info(f"         失败原因: {'; '.join(r.fail_reasons)}")

    # 保存到 CSV（始终写固定路径；--output 可额外指定另一路径）
    import pandas as pd

    df = pd.DataFrame([r.to_dict() for r in results])
    exist_cols = [c for c in _FIVEMUST_FIELDS if c in df.columns]
    df = df[exist_cols]
    df.to_csv(default_output, index=False, encoding="utf-8-sig")
    logger.info(f"💾 五必结果已写入: {default_output}（{len(results)} 只）")
    if args.output and args.output != str(default_output):
        df.to_csv(args.output, index=False, encoding="utf-8-sig")
        logger.info(f"💾 五必结果已额外写入: {args.output}")

    # 飞书推送（即使无股票完全通过，也推送筛选摘要）
    if not args.no_push:
        try:
            from scripts.feishu_utils import push_feishu

            if passed:
                lines = [
                    f"📈 **A股五必筛选结果**（共 {len(passed)} 只通过全部5条）\n"
                ]
                for r in passed[:20]:
                    ma5_str = f"{r.ma5:.2f}" if r.ma5 is not None else "N/A"
                    lines.append(
                        f"  {r.code} {r.name[:10]:<10} "
                        f"收盘={r.close} "
                        f"分位={r.price_percentile} "
                        f"ROE={r.roe} "
                        f"换手率={r.turnover_rate_20d} "
                        f"MA5={ma5_str} "
                        f"规则={r.pass_count}/{r.total_rules}"
                    )
            else:
                lines = [
                    f"📈 **A股五必筛选结果**（共 {len(results)} 只候选，0 只通过全部5条）",
                    "条件：历史低位(3年分位≤30%) / 底部扎实(20日波动≤25%+5日涨幅≤15%) / 业绩优秀(ROE≥10%+扣非≥10%+净利>0+现金流>0) / 筹码集中(换手率<1.5%或股东户数环比减少) / 日K多头突破(MA5>MA13+收盘>MA5)",
                    "",
                    f"各条件通过数：历史低位 {sum(1 for r in results if r.cond1_history_low)} / 底部扎实 {sum(1 for r in results if r.cond2_bottom_solid)} / 筹码集中 {sum(1 for r in results if r.cond4_concentration)} / 业绩优秀 {sum(1 for r in results if r.cond3_performance)} / 日K多头突破 {sum(1 for r in results if r.cond5_breakout)}",
                    "⚠️ 无股票同时满足全部 5 个条件，建议放宽条件后重试。",
                ]
            push_feishu("text", content="\n".join(lines), title="A股五必筛选结果")
            logger.info("✅ 飞书推送成功")
        except Exception as e:
            logger.warning(f"⚠️ 飞书推送失败: {e}")

    logger.info("✅ 五必筛选完成")


if __name__ == "__main__":
    main()
