#!/usr/bin/env python3
"""港股筛选器 CLI 入口。

根据用户提供的详细规则表，对全量港股进行多维度筛选。

用法：
    uv run python scripts/hk_screen/cli.py                    # 全量筛选
    uv run python scripts/hk_screen/cli.py --top 20           # 只输出前 20 名
    uv run python scripts/hk_screen/cli.py --min-pass-rate 0.8  # 最小通过比例 80%
    uv run python scripts/hk_screen/cli.py --output result.csv  # 输出到 CSV
    uv run python scripts/hk_screen/cli.py --no-push           # 不推送飞书
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from loguru import logger

from scripts.hk_screen import (
    compute_quality_score,
    fetch_hk_raw_data,
    get_hk_connect_stocks,
    get_hk_stock_list_from_cache,
    print_screen_summary,
    raw_to_screen_result,
    screen_hk_stocks,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="港股筛选器")
    parser.add_argument(
        "--top",
        type=int,
        default=0,
        help="只输出前 N 名（0=全部）",
    )
    parser.add_argument(
        "--min-pass-rate",
        type=float,
        default=1.0,
        help="最小通过比例（1.0=必须通过所有规则，0.8=通过80%以上规则）",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="输出 CSV 文件路径（不指定则不保存）",
    )
    parser.add_argument(
        "--no-push",
        action="store_true",
        help="不推送飞书",
    )
    parser.add_argument(
        "--require-hk-connect",
        action="store_true",
        help="强制要求港股通资格（默认不强制，数据缺失时不剔除）",
    )
    args = parser.parse_args()

    # 获取港股通成分股清单
    hk_connect_stocks = get_hk_connect_stocks()

    # 优先从本地缓存获取股票列表
    logger.info("📊 正在从本地缓存获取港股列表...")
    tickers = get_hk_stock_list_from_cache()
    if not tickers:
        logger.warning("⚠️  本地缓存无港股数据，将尝试从 akshare 获取全量列表")
        from scripts.hk_screen.data_fetcher import get_hk_stock_list_from_akshare

        tickers = get_hk_stock_list_from_akshare()
    logger.info(f"📋 待筛选港股数量: {len(tickers)} 只")

    # 获取全量港股原始数据（优先从本地缓存读取 K 线）
    logger.info("🚀 开始获取全量港股数据...")
    raw_data = fetch_hk_raw_data(
        tickers=tickers,
        hk_connect_stocks=hk_connect_stocks,
    )

    # 转换为筛选结果
    results = [raw_to_screen_result(r) for r in raw_data]
    logger.info(f"📊 共获取 {len(results)} 只港股原始数据")

    # 执行筛选
    logger.info("🔍 开始执行筛选规则...")
    passed = screen_hk_stocks(
        results,
        min_pass_rate=args.min_pass_rate,
        require_hk_connect=args.require_hk_connect,
    )

    # 打印摘要
    print_screen_summary(passed)

    # 输出前 N 名
    top_results = passed[: args.top] if args.top > 0 else passed

    logger.info("\n📋 筛选通过股票明细:")
    for i, r in enumerate(top_results, 1):
        pe_str = f"{r.pe_ttm:.1f}" if r.pe_ttm is not None else "N/A"
        roe_str = f"{r.avg_roe_3y:.1%}" if r.avg_roe_3y is not None else "N/A"
        cap_str = f"{r.market_cap / 1e8:.0f}亿" if r.market_cap is not None else "N/A"
        logger.info(
            f"  {i:3d}. {r.ticker} {r.name:<20} "
            f"市值={cap_str} "
            f"PE={pe_str} "
            f"ROE={roe_str} "
            f"质量分={compute_quality_score(r):.0f} "
            f"规则={r.pass_count}/{r.total_rules}"
        )

    # 保存到 CSV
    if args.output:
        import pandas as pd

        df = pd.DataFrame([r.to_dict() for r in passed])
        df.to_csv(args.output, index=False, encoding="utf-8-sig")
        logger.info(f"💾 结果已保存到: {args.output}")

    # 飞书推送（如果启用）
    if not args.no_push and passed:
        try:
            from scripts.feishu_utils import push_feishu

            lines = [f"📈 **港股筛选结果**（共 {len(passed)} 只通过）\n"]
            for r in top_results[:20]:
                pe_str = f"{r.pe_ttm:.1f}" if r.pe_ttm is not None and not pd.isna(r.pe_ttm) else "N/A"
                roe_str = f"{r.avg_roe_3y:.1%}" if r.avg_roe_3y is not None and not pd.isna(r.avg_roe_3y) else "N/A"
                cap_str = f"{r.market_cap / 1e8:.0f}亿" if r.market_cap is not None else "N/A"
                lines.append(
                    f"  {r.ticker} {r.name[:10]:<10} "
                    f"市值={cap_str} "
                    f"PE={pe_str} "
                    f"ROE={roe_str}"
                )
            push_feishu("text", content="\n".join(lines), title="港股筛选结果")
            logger.info("✅ 飞书推送成功")
        except Exception as e:
            logger.warning(f"⚠️ 飞书推送失败: {e}")

    logger.info("✅ 港股筛选完成")


if __name__ == "__main__":
    main()
