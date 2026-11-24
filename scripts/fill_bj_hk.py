#!/usr/bin/env python3
"""补齐北交所与港股的增量 K 线数据（安全版，只追加不覆盖）。

设计要点：
- 目标日期动态取 :func:`scripts._utils.expected_trading_day`，不再硬编码；
- 使用 :func:`scripts.fill_missing_latest.fetch_and_append` 严格增量追加，
  只写入 ``end`` 之后的新行，**不会**把已有较新数据回退；
- 数据库路径走 :func:`scripts._utils.get_cache_db_path`，兼容测试隔离与任意工作目录；
- 港股 Provider 链由 ``configs/providers.yaml`` 的 ``hk`` 段决定（``akshare_hk`` 优先）。

用法：
    uv run python scripts/fill_bj_hk.py                  # 补 hk + bj 到最近交易日
    uv run python scripts/fill_bj_hk.py --markets hk     # 只补港股
    uv run python scripts/fill_bj_hk.py --target 2026-11-21
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
from loguru import logger

from scripts._utils import expected_trading_day, get_cache_db_path, get_sqlite_conn
from scripts.fill_missing_latest import fetch_and_append
from trade_krono_cli.cli_commands.core import _load_env
from trade_krono_cli.data_providers.factory import get_data_factory

# 增量窗口：以目标日前 30 个自然日覆盖长假与短时停牌
WINDOW_DAYS = 30


def _get_stale_tickers(prefix: str, target: str) -> list[str]:
    """返回指定前缀下 ``end`` 早于目标日期的 ticker。

    Args:
        prefix: ``"hk."`` 或 ``"bj."``。
        target: 目标日期 ``YYYY-MM-DD``。

    Returns:
        滞后 ticker 列表（按代码升序）。
    """
    db_path = get_cache_db_path()
    if not db_path.exists():
        logger.error(f"❌ 缓存数据库不存在: {db_path}")
        return []
    conn = get_sqlite_conn(db_path, readonly=True)
    try:
        rows = conn.execute(
            "SELECT ticker FROM kline_cache WHERE ticker LIKE ? AND end < ? ORDER BY ticker",
            (f"{prefix}%", target),
        ).fetchall()
    finally:
        conn.close()
    return [r[0] for r in rows]


def main() -> int:
    """执行增量补齐。

    Returns:
        进程退出码（0 成功或无需补齐，1 全部失败）。
    """
    parser = argparse.ArgumentParser(description="补齐北交所/港股增量 K 线")
    parser.add_argument("--markets", choices=["all", "hk", "bj"], default="all")
    parser.add_argument("--target", default=None, help="目标日期（默认最近交易日）")
    parser.add_argument("--limit", type=int, default=0, help="每个市场最多处理 N 只")
    args = parser.parse_args()

    _load_env()

    target = args.target or expected_trading_day()
    fetch_start = (pd.Timestamp(target) - pd.Timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%d")
    logger.info(f"📅 增量目标 {target}，拉取窗口 {fetch_start} ~ {target}")

    markets: list[tuple[str, str]] = []
    if args.markets in ("all", "hk"):
        markets.append(("hk.", "HK"))
    if args.markets in ("all", "bj"):
        markets.append(("bj.", "BJ"))

    factory = get_data_factory()
    total_ok = total_fail = 0

    for prefix, label in markets:
        stale = _get_stale_tickers(prefix, target)
        if args.limit > 0:
            stale = stale[: args.limit]
        logger.info(f"{label} 待补齐 (end < {target}): {len(stale)} 只")

        ok = fail = 0
        for i, ticker in enumerate(stale, 1):
            _, succeeded, detail = fetch_and_append(factory, ticker, fetch_start, target)
            if succeeded:
                ok += 1
            else:
                fail += 1
                logger.warning(f"  ❌ {ticker}: {detail}")
            if i % 20 == 0 or i == len(stale):
                logger.info(f"  [{i}/{len(stale)}] {label} ok={ok} fail={fail}")
        total_ok += ok
        total_fail += fail
        logger.info(f"{label} 填充完成：成功 {ok}，失败 {fail}，总计 {len(stale)}\n")

    logger.info(f"=== 总计：成功 {total_ok}，失败 {total_fail} ===")
    return 0 if total_fail == 0 or total_ok > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
