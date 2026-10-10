#!/usr/bin/env python3
"""拉取今日增量 K 线数据，补齐所有股票的最新交易日数据。

用法：
  uv run python scripts/fill_holiday_gap.py
"""

from __future__ import annotations

import io
import pickle
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger

from trade_krono_cli.cli_commands.core import _load_env
from trade_krono_cli.data_providers.factory import get_data_factory

CACHE_DB = Path("outputs/cache/pipeline_cache.db")
WORKERS = 15
BATCH_LOG = 500


def get_stale_tickers(target_date: str) -> list[str]:
    """获取 end < target_date 的所有股票。"""
    conn = sqlite3.connect(str(CACHE_DB))
    rows = conn.execute(
        "SELECT DISTINCT ticker FROM kline_cache "
        "WHERE freq='d' AND adjustflag='1' AND end < ? ORDER BY ticker",
        (target_date,),
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def fill_one(factory, ticker: str, target_date: str) -> tuple[str, bool, str]:
    """拉取并合并增量数据。"""
    try:
        provider = factory.get_provider("tonghuashun")
        if provider is None:
            return (ticker, False, "no_provider")

        result = provider.fetch_kline(ticker, target_date, target_date, frequency="d", adjustflag="1")
        if result is None or result.is_empty:
            return (ticker, False, "empty")

        df = result.to_dataframe()
        conn = sqlite3.connect(str(CACHE_DB))
        row = conn.execute(
            "SELECT data FROM kline_cache WHERE ticker=? AND freq=? AND adjustflag=?",
            (ticker, "d", "1"),
        ).fetchone()
        conn.close()

        if row is None:
            return (ticker, False, "no_record")

        old_data = pickle.loads(io.BytesIO(row[0]).read())
        if hasattr(old_data, "to_dataframe"):
            old_df = old_data.to_dataframe()
        else:
            old_df = old_data
        merged = pd.concat([old_df, df], ignore_index=True)
        merged = merged.drop_duplicates(subset=["timestamps"], keep="last")
        merged = merged.sort_values("timestamps").reset_index(drop=True)

        new_pickle = io.BytesIO()
        pd.to_pickle(merged, new_pickle)
        new_pickle.seek(0)

        conn = sqlite3.connect(str(CACHE_DB))
        conn.execute(
            "UPDATE kline_cache SET data=?, end=? WHERE ticker=? AND freq=? AND adjustflag=?",
            (new_pickle.read(), target_date, ticker, "d", "1"),
        )
        conn.commit()
        conn.close()

        return (ticker, True, f"filled {len(df)} days")
    except Exception as e:
        return (ticker, False, str(e)[:80])


def main() -> None:
    _load_env()
    today = date.today().isoformat()
    target_date = today

    stale = get_stale_tickers(target_date)
    logger.info(f"需补齐 {target_date} 数据: {len(stale)} 只")

    if not stale:
        logger.info("数据已是最新。")
        return

    factory = get_data_factory()
    success = 0
    failed = 0
    start = time.time()

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(fill_one, factory, t, target_date): t for t in stale}
        for future in as_completed(futures):
            ticker, ok, msg = future.result()
            if ok:
                success += 1
            else:
                failed += 1
            elapsed = time.time() - start
            if (success + failed) % BATCH_LOG == 0 or success + failed == len(stale):
                rate = (success + failed) / elapsed * 3600 if elapsed > 0 else 0
                logger.info(
                    f"进度: {success + failed}/{len(stale)} "
                    f"(成功={success}, 失败={failed}, 速率={rate:.0f}/h)"
                )

    elapsed = time.time() - start
    logger.info(f"完成！成功={success}, 失败={failed}, 耗时={elapsed:.0f}s")


if __name__ == "__main__":
    main()
