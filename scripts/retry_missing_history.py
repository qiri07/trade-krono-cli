#!/usr/bin/env python3
"""重拉取全量同步失败的历史数据（安全版，追加不覆盖）。

本脚本已重构：原版使用 `cache.set_kline(...)` 直接写入（可能整行覆盖已有数据），
现已改为基于 :mod:`scripts.fill_missing_latest` 的 `fetch_and_append`
（严格增量，仅追加 end 之后的行），避免数据回退。

用法：
  uv run python scripts/retry_missing_history.py   # 重拉缺少 2026-11-04 数据的股票
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


from scripts._utils import _get_expected_date, get_sqlite_conn
from scripts.fill_missing_latest import fetch_and_append
from trade_krono_cli.cli_commands.core import _load_env
from trade_krono_cli.data_providers.factory import get_data_factory

START_DATE = "2020-01-01"
END_DATE = _get_expected_date()  # 最近已收盘交易日
WORKERS = 16
BATCH_SIZE = 64
BATCH_DELAY = 2.0
CACHE_DB = Path("outputs/cache/pipeline_cache.db")

_load_env()


def get_incomplete_tickers() -> list[str]:
    """获取缺少目标日期数据的股票（非北交所）。"""
    conn = get_sqlite_conn(CACHE_DB)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT DISTINCT k.ticker FROM kline_cache k
            WHERE k.end < ?
            ORDER BY k.ticker
            """,
            (END_DATE,),
        )
        tickers = [r[0] for r in cur.fetchall()]
    finally:
        conn.close()
    return tickers


def main() -> None:
    tickers = get_incomplete_tickers()
    if not tickers:
        print("✅ 没有需要重拉的股票")
        return

    print(f"🚀 发现 {len(tickers)} 只缺少历史数据的股票，开始重试（增量追加，不覆盖历史）...")
    print(f"   范围: {START_DATE} ~ {END_DATE}")
    print()

    factory = get_data_factory()
    success = failed = 0
    failed_list: list[tuple[str, str]] = []
    total = len(tickers)

    for batch_start in range(0, total, BATCH_SIZE):
        batch = tickers[batch_start : batch_start + BATCH_SIZE]
        batch_num = batch_start // BATCH_SIZE + 1
        print(f"  批次 {batch_num} ({batch_start + 1}~{min(batch_start + BATCH_SIZE, total)}/{total})")

        # 串行处理批次内的每只股票（避免并发登录污染）
        for ticker in batch:
            _, succeeded, detail = fetch_and_append(factory, ticker, START_DATE, END_DATE)
            if succeeded:
                success += 1
                print(f"    ✅ {ticker}: {detail}")
            else:
                failed += 1
                failed_list.append((ticker, detail))
                print(f"    ❌ {ticker}: {detail}")

        # 批次间短暂休息
        if batch_start + BATCH_SIZE < total:
            time.sleep(BATCH_DELAY)

    print()
    print("=" * 50)
    print(f"✅ 成功: {success}/{total}")
    print(f"❌ 失败: {failed}/{total}")
    if failed_list:
        print("\n失败股票（前20只）:")
        for t, reason in failed_list[:20]:
            print(f"  {t}: {reason}")
    print("=" * 50)


if __name__ == "__main__":
    main()
