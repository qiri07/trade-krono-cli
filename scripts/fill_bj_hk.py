#!/usr/bin/env python3
"""补齐北交所和港股的增量 K 线数据（安全版，追加不覆盖）。

本脚本已重构：原版使用硬编码 `_END_DATE="2026-10-13"` 并做整行覆盖（会把已有
较新数据回退），现已改为基于 :mod:`scripts.fill_missing_latest` 的 `fetch_and_append`
（严格增量，仅追加 end 之后的行），避免数据回退。

用法：
  uv run python scripts/fill_bj_hk.py   # 补齐 hk./bj. 板块滞后于 2026-11-04 的增量数据
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger

from scripts._utils import get_sqlite_conn
from scripts.fill_missing_latest import fetch_and_append
from trade_krono_cli.cli_commands.core import _load_env
from trade_krono_cli.data_providers.factory import get_data_factory

CACHE_DB = Path("outputs/cache/pipeline_cache.db")
START, END = "2026-10-17", "2026-11-04"
TARGET = "2026-11-04"

_load_env()


def _get_tickers(prefix: str) -> list[str]:
    """读取指定前缀（hk./bj.）的股票代码列表。"""
    conn = get_sqlite_conn(CACHE_DB)
    try:
        rows = conn.execute(
            "SELECT ticker FROM kline_cache WHERE ticker LIKE ? ORDER BY ticker",
            (f"{prefix}%",),
        ).fetchall()
    finally:
        conn.close()
    return [r[0] for r in rows]


def main() -> None:
    factory = get_data_factory()
    total_ok = total_fail = 0

    for prefix, label in (("hk.", "HK"), ("bj.", "BJ")):
        # 只补齐滞后于 TARGET 的标的
        conn = get_sqlite_conn(CACHE_DB)
        try:
            rows = conn.execute(
                "SELECT ticker FROM kline_cache "
                "WHERE ticker LIKE ? AND end < ? ORDER BY ticker",
                (prefix, TARGET),
            ).fetchall()
        finally:
            conn.close()
        stale = [r[0] for r in rows]

        logger.info(f"{label} 待补齐 (end < {TARGET}): {len(stale)} 只")
        ok = fail = 0
        for i, ticker in enumerate(stale, 1):
            _, succeeded, detail = fetch_and_append(factory, ticker, START, END)
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


if __name__ == "__main__":
    main()
