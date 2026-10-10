#!/usr/bin/env python3
"""拉取今日增量 K 线数据，补齐所有股票的最新交易日数据。

用法：
  uv run python scripts/fetch_today_increment.py
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


def get_max_end_date() -> str | None:
    """获取数据库中最新的 end 日期。"""
    conn = sqlite3.connect(str(CACHE_DB))
    row = conn.execute(
        "SELECT MAX(end) FROM kline_cache WHERE freq='d' AND adjustflag='1'"
    ).fetchone()
    conn.close()
    return row[0] if row and row[0] else None


def get_stale_tickers(max_end: str) -> list[str]:
    """获取 end < max_end 的所有股票（需要同步到最新日期的）。"""
    conn = sqlite3.connect(str(CACHE_DB))
    rows = conn.execute(
        "SELECT DISTINCT ticker FROM kline_cache "
        "WHERE freq='d' AND adjustflag='1' AND end < ? ORDER BY ticker",
        (max_end,),
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def fill_one(factory, ticker: str, target_date: str) -> tuple[str, bool, str]:
    """拉取单只股票的目标日期数据并更新 DB。"""
    try:
        provider = factory.get_provider("tonghuashun")
        if provider is None:
            return (ticker, False, "no_provider")

        result = provider.fetch_kline(ticker, target_date, target_date, frequency="d", adjustflag="1")
        if result is None or result.is_empty:
            return (ticker, False, "empty")

        new_df = result.to_dataframe()
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

        merged = pd.concat([old_df, new_df], ignore_index=True)
        merged = merged.drop_duplicates(subset=["timestamps"], keep="last")
        merged = merged.sort_values("timestamps").reset_index(drop=True)

        buf = io.BytesIO()
        pd.to_pickle(merged, buf)
        buf.seek(0)

        conn = sqlite3.connect(str(CACHE_DB))
        conn.execute(
            "UPDATE kline_cache SET data=?, end=? WHERE ticker=? AND freq=? AND adjustflag=?",
            (buf.read(), target_date, ticker, "d", "1"),
        )
        conn.commit()
        conn.close()
        return (ticker, True, "ok")
    except Exception as e:
        return (ticker, False, str(e)[:60])


def main() -> None:
    _load_env()

    today = date.today().isoformat()
    max_end = get_max_end_date()
    logger.info(f"当前最新数据日期: {max_end}, 今日: {today}")

    if max_end == today:
        logger.info("数据已是最新，无需更新。")
        return

    target_date = today  # 拉取今日数据
    stale = get_stale_tickers(target_date)
    logger.info(f"需同步 {len(stale)} 只股票 → {target_date}")

    if not stale:
        logger.info("无缺失数据。")
        return

    factory = get_data_factory()
    success = failed = 0
    start = time.time()

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(fill_one, factory, t, target_date): t for t in stale}
        for f in as_completed(futures):
            _, ok, _ = f.result()
            if ok:
                success += 1
            else:
                failed += 1
            if (success + failed) % BATCH_LOG == 0 or success + failed == len(stale):
                elapsed = time.time() - start
                rate = (success + failed) / elapsed * 3600 if elapsed > 0 else 0
                logger.info(
                    f"进度: {success + failed}/{len(stale)} "
                    f"(成功={success}, 失败={failed}, 速率={rate:.0f}/h)"
                )

    elapsed = time.time() - start
    logger.info(f"完成！成功={success}, 失败={failed}, 耗时={elapsed:.0f}s")


if __name__ == "__main__":
    main()
