#!/usr/bin/env python3
"""增量同步：补齐 2026-09-17 及之后缺失的 K 线数据。

策略：
  - tonghuashun 为主（覆盖沪深+北交所，延迟最低）
  - baostock 为备（非北交所，免费无限流）
  - 20 并发，批次间隔 0.2s
"""

from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
from loguru import logger

from trade_krono_cli.cache import get_cache
from trade_krono_cli.data_providers.factory import get_data_factory

WORKERS = 20
BATCH_SIZE = 100
FETCH_TIMEOUT = 30
CACHE_DB = Path("outputs/cache/pipeline_cache.db")
SYNC_START = "2026-09-15"
SYNC_END = "2026-09-17"


def get_missing_tickers() -> list[str]:
    """获取缺少 SYNC_END 数据的 ticker。"""
    import sqlite3

    conn = sqlite3.connect(str(CACHE_DB))
    rows = conn.execute(
        "SELECT ticker FROM kline_cache WHERE end < ? ORDER BY ticker",
        (SYNC_END,),
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def fetch_one(factory, ticker: str) -> tuple[str, bool, str]:
    """拉取增量数据并追加到现有记录。"""
    try:
        # 非北交所：tonghuashun 主，baostock 备
        # 北交所：只用 tonghuashun
        if ticker.startswith("bj."):
            providers = ["tonghuashun"]
        else:
            providers = ["tonghuashun", "baostock"]

        result = None
        used_provider = ""
        for pname in providers:
            provider = factory.get_provider(pname)
            if provider is None:
                continue
            try:
                result = provider.fetch_kline(
                    ticker, SYNC_START, SYNC_END, frequency="d", adjustflag="1"
                )
                if result is not None and not result.is_empty:
                    used_provider = pname
                    break
            except Exception:
                continue

        if result is None or result.is_empty:
            return (ticker, False, "no_data")

        df = result.to_dataframe()
        if df is None or len(df) == 0:
            return (ticker, False, "empty_df")

        ts_col = "timestamps" if "timestamps" in df.columns else "date"
        ts = pd.to_datetime(df[ts_col])

        import sqlite3

        conn = sqlite3.connect(str(CACHE_DB))
        row = conn.execute(
            "SELECT start, end, data FROM kline_cache WHERE ticker = ?", (ticker,)
        ).fetchone()
        conn.close()

        if row:
            existing_start, existing_end, existing_data = row
            try:
                from io import BytesIO

                existing_df = pd.read_pickle(BytesIO(existing_data))
            except Exception:
                existing_df = pd.DataFrame()
            # 真正的增量：严格晚于现有最后日期
            new_rows = df[ts > pd.Timestamp(existing_end)]
            if len(new_rows) == 0:
                return (ticker, True, "already_up_to_date")
            combined = pd.concat([existing_df, new_rows], ignore_index=True)
        else:
            combined = df

        cache = get_cache()
        cache.set_kline(
            ticker,
            combined[ts_col].iloc[0].strftime("%Y-%m-%d"),
            combined[ts_col].iloc[-1].strftime("%Y-%m-%d"),
            freq="d",
            df=combined,
            ttl=0.0,
            adjustflag="1",
        )
        n_new = len(combined) - len(existing_df) if row else len(combined)
        return (ticker, True, f"{n_new}rows@{used_provider}")
    except Exception as e:
        return (ticker, False, str(e)[:80])


def main() -> None:
    logger.info("=" * 60)
    logger.info(f"增量同步：补齐 {SYNC_START} ~ {SYNC_END} K 线数据")
    logger.info("=" * 60)

    tickers = get_missing_tickers()
    bj = [t for t in tickers if t.startswith("bj.")]
    shsz = [t for t in tickers if not t.startswith("bj.")]
    logger.info(f"需同步 {len(tickers)} 只: bj={len(bj)}, sh/sz={len(shsz)}")

    if not tickers:
        logger.info("无需同步，数据已是最新")
        return

    factory = get_data_factory()
    all_batches = [tickers[i : i + BATCH_SIZE] for i in range(0, len(tickers), BATCH_SIZE)]
    logger.info(f"分 {len(all_batches)} 批，每批 {BATCH_SIZE} 只\n")

    total_ok = 0
    total_fail = 0
    failed_list: list[tuple[str, str]] = []

    for batch_idx, batch in enumerate(all_batches):
        t0 = time.time()
        ok = 0
        fail = 0
        batch_failed: list[tuple[str, str]] = []

        with ThreadPoolExecutor(max_workers=WORKERS) as executor:
            futures = {executor.submit(fetch_one, factory, t): t for t in batch}
            for future in as_completed(futures, timeout=FETCH_TIMEOUT * len(batch) // WORKERS + 10):
                ticker, succeeded, detail = future.result()
                if succeeded:
                    ok += 1
                else:
                    fail += 1
                    batch_failed.append((ticker, detail))

        elapsed = time.time() - t0
        total_ok += ok
        total_fail += fail
        failed_list.extend(batch_failed)
        logger.info(
            f"  批次 {batch_idx + 1}/{len(all_batches)}: 成功 {ok}/{len(batch)}, "
            f"失败 {fail}, 耗时 {elapsed:.1f}s"
        )
        time.sleep(0.2)

    logger.info("\n" + "=" * 60)
    logger.info(f"完成！成功 {total_ok}，失败 {total_fail}，总计 {len(tickers)}")
    if failed_list:
        logger.warning(
            f"失败股票 ({len(failed_list)}): {', '.join(t for t, _ in failed_list[:30])}"
        )
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
