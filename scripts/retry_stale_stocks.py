#!/usr/bin/env python3
"""重试补齐所有滞后股票的最新交易日数据。

直接尝试拉取 2026-10-08（国庆后首个交易日）的数据，而非逐个日期。
优先级：tonghuashun（主）→ baostock（降）→ akshare（降）。
"""

from __future__ import annotations

import io
import pickle
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger

from trade_krono_cli.cli_commands.core import _load_env
from trade_krono_cli.data_providers.factory import get_data_factory

CACHE_DB = Path("outputs/cache/pipeline_cache.db")
TARGET_DATE = "2026-10-08"
WORKERS = 15
PROVIDERS = ["tonghuashun", "baostock", "akshare"]


def get_stale_tickers() -> list[str]:
    """获取 max_end < TARGET_DATE 的所有 ticker。"""
    conn = sqlite3.connect(str(CACHE_DB))
    rows = conn.execute(
        "SELECT ticker FROM kline_cache "
        "WHERE freq='d' AND adjustflag='1' "
        "GROUP BY ticker HAVING MAX(end) < ? "
        "ORDER BY ticker",
        (TARGET_DATE,),
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def fetch_and_merge(factory, ticker: str) -> tuple[str, bool, str]:
    """尝试用多个 provider 拉取 TARGET_DATE 数据并合并到 DB。"""
    for provider_name in PROVIDERS:
        provider = factory.get_provider(provider_name)
        if provider is None:
            continue
        try:
            result = provider.fetch_kline(
                ticker, TARGET_DATE, TARGET_DATE,
                frequency="d", adjustflag="1",
            )
            if result is None or result.is_empty:
                continue

            df = result.to_dataframe()
            conn = sqlite3.connect(str(CACHE_DB))
            row = conn.execute(
                "SELECT data FROM kline_cache WHERE ticker=? AND freq=? AND adjustflag=?",
                (ticker, "d", "1"),
            ).fetchone()
            conn.close()

            if row is None:
                continue

            old_data = pickle.loads(io.BytesIO(row[0]).read())
            if hasattr(old_data, "to_dataframe"):
                old_df = old_data.to_dataframe()
            else:
                old_df = old_data

            merged = pd.concat([old_df, df], ignore_index=True)
            merged = merged.drop_duplicates(subset=["timestamps"], keep="last")
            merged = merged.sort_values("timestamps").reset_index(drop=True)

            buf = io.BytesIO()
            pd.to_pickle(merged, buf)
            buf.seek(0)

            conn = sqlite3.connect(str(CACHE_DB))
            conn.execute(
                "UPDATE kline_cache SET data=?, end=? WHERE ticker=? AND freq=? AND adjustflag=?",
                (buf.read(), TARGET_DATE, ticker, "d", "1"),
            )
            conn.commit()
            conn.close()
            return (ticker, True, provider_name)
        except Exception as e:
            logger.debug(f"{provider_name} 重试 {ticker}: {str(e)[:60]}")
            continue
    return (ticker, False, "all_failed")


def main() -> None:
    _load_env()
    stale = get_stale_tickers()
    logger.info(f"🔄 需补齐 {len(stale)} 只滞后股票 → {TARGET_DATE}")

    if not stale:
        logger.info("数据已是最新。")
        return

    factory = get_data_factory()
    ok = failed = 0
    start = time.time()

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(fetch_and_merge, factory, t): t for t in stale}
        for f in as_completed(futures):
            _, succeeded, _ = f.result()
            if succeeded:
                ok += 1
            else:
                failed += 1
            elapsed = time.time() - start
            if (ok + failed) % 25 == 0 or ok + failed == len(stale):
                rate = (ok + failed) / elapsed * 3600 if elapsed > 0 else 0
                logger.info(
                    f"进度: {ok + failed}/{len(stale)} "
                    f"(成功={ok}, 失败={failed}, 速率={rate:.0f}/h)"
                )

    elapsed = time.time() - start
    logger.info(f"✅ 完成！成功={ok}, 失败={failed}, 耗时={elapsed:.0f}s")


if __name__ == "__main__":
    main()
