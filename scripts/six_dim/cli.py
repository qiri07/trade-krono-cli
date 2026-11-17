"""six_dim 选股 — CLI 入口。

提供命令行入口，可直接运行 `uv run python -m scripts.six_dim`。
"""

from __future__ import annotations

import argparse
from pathlib import Path

from scripts._utils import expected_trading_day
from scripts.six_dim.cards import build_feishu_card
from scripts.six_dim.constants import MIN_MARKET_CAP_YI
from scripts.six_dim.report import write_report
from scripts.six_dim.universe import screen


def main(argv: list[str] | None = None) -> int:
    """六维选股 CLI 入口。

    Args:
        argv: 命令行参数列表，缺省使用 sys.argv[1:]。

    Returns:
        退出码（0 成功，1 失败）。
    """
    parser = argparse.ArgumentParser(description="六维选股筛选（逻辑 > 估值 > 位置 > 趋势 > 股东人数 > 量价）")
    parser.add_argument(
        "--date",
        default=expected_trading_day(),
        help="分析日期 YYYY-MM-DD（默认最近交易日）",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=10,
        help="推荐前 N 只（0 表示输出全部通过门槛的股票）",
    )
    parser.add_argument(
        "--min-cap",
        type=float,
        default=MIN_MARKET_CAP_YI,
        help=f"最小总市值（亿元，默认 {MIN_MARKET_CAP_YI:g}）",
    )
    parser.add_argument(
        "--no-fetch",
        action="store_true",
        help="仅用本地缓存（不查估值分位/股东户数/快照）",
    )
    parser.add_argument(
        "--max-candidates",
        type=int,
        default=800,
        help="进入技术面计算的最大候选数",
    )
    parser.add_argument(
        "--push",
        type=int,
        default=0,
        help="是否推送飞书（1=是，0=否，默认 0）",
    )
    parser.add_argument(
        "--out-dir",
        default=None,
        help="报告输出目录（默认 outputs/results）",
    )

    args = parser.parse_args(argv)

    results, funnel = screen(
        date_str=args.date,
        min_cap=args.min_cap,
        fetch=not args.no_fetch,
        max_candidates=args.max_candidates,
    )

    display_results = results[: args.top] if args.top > 0 else results

    # 控制台输出
    print(f"\n{'='*60}")
    print(f"六维选股结果 — {args.date}")
    print("排序：分档字典序（逻辑 > 估值 > 位置 > 趋势 > 股东人数 > 量价）")
    print(f"{'='*60}\n")
    if funnel:
        print(
            f"候选漏斗：A股 {funnel.get('universe', 0)} → "
            f"逻辑 {funnel.get('logic_pass', 0)} → "
            f"绝对估值 {funnel.get('abs_val_pass', 0)} → "
            f"分位/PEG {funnel.get('val_pass', 0)} → "
            f"趋势 {funnel.get('trend_pass', 0)} → 精选 {len(results)}"
        )
        print()
    for i, r in enumerate(display_results, 1):
        print(
            f"{i}. {r.name}（{r.code}）- 逻辑{r.logic:.1f}分 综合{r.composite:.1f}分"
        )
        print(
            f"   逻辑{r.logic:.0f} 估值{r.valuation:.0f} 位置{r.position:.0f} "
            f"趋势{r.trend:.0f} 股东{r.holder:.0f} 量价{r.vol_price:.0f}"
        )
    print(f"\n{'='*60}")
    print(f"六维选股完成：共 {len(results)} 只通过门槛，显示前 {len(display_results)} 只")
    print(f"{'='*60}\n")

    # 写报告
    from trade_krono_cli.config import get_settings

    out_dir = Path(args.out_dir) if args.out_dir else Path(get_settings().results_dir)
    json_path, md_path = write_report(
        display_results,
        args.date,
        out_dir,
        funnel=funnel,
        total_passed=len(results),
    )
    print(f"JSON 报告：{json_path}")
    print(f"MD 报告：{md_path}")

    # 飞书推送
    if args.push:
        from scripts.feishu_utils import push_feishu

        card = build_feishu_card(display_results, args.date, funnel)
        push_feishu("text", content=card)
        print("飞书卡片已推送")

    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
