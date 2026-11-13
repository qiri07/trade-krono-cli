"""double_ma 选股 — CLI 入口。

命令行入口：--lite 轻量模式（MA+PE+MACD），默认完整模式（+ROE+净利润）。
"""

from __future__ import annotations

import argparse
from pathlib import Path

from loguru import logger

from scripts._utils import expected_trading_day
from scripts.double_ma.screener import run_full, run_lite


def main() -> None:
    """命令行入口：--lite 轻量模式（MA+PE+MACD），默认完整模式（+ROE+净利润）。"""
    parser = argparse.ArgumentParser(description="双均线金叉筛选器（统一版）")
    parser.add_argument(
        "--lite",
        action="store_true",
        help="轻量模式：仅 MA+PE+MACD 技术分析（默认完整模式含 ROE+净利润）",
    )
    parser.add_argument(
        "--date",
        default=expected_trading_day(),
        help="分析日期 YYYY-MM-DD，用作结果文件命名（默认最近交易日）",
    )
    args = parser.parse_args()

    date_tag = args.date
    output_dir = Path("outputs/results")
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.lite:
        output_file = output_dir / f"double_ma_golden_cross_pe_macd_{date_tag}.json"
        logger.info("轻量模式：仅 MA+PE+MACD 技术分析筛选")
        results = run_lite(output_file)
        print(f"RESULT_FILE={output_file}")
        print(f"COUNT={len(results)}")
        return

    phase1_file = output_dir / f"double_ma_golden_cross_pe_macd_{date_tag}.json"
    output_file = output_dir / f"double_ma_golden_cross_{date_tag}.json"
    results = run_full(output_file, phase1_file)
    print(f"RESULT_FILE={output_file}")
    print(f"COUNT={len(results)}")


if __name__ == "__main__":
    main()
