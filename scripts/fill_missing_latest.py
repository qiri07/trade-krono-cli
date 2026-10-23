#!/usr/bin/env python3
"""补齐缺少最新数据的股票增量数据。

默认从增量同步失败中提取的股票（缺 2026-09-14 数据），统一走 tonghuashun 重试。

可通过命令行参数自定义目标最新日期与拉取窗口：
    python scripts/fill_missing_latest.py                    # 默认 9-01~10-07, 缺 9-14
    python scripts/fill_missing_latest.py --before 2026-10-16 --start 2026-09-01 --end 2026-10-19
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
from loguru import logger

from scripts._utils import get_provider_chain
from trade_krono_cli.cache import get_cache
from trade_krono_cli.cli_commands.core import _load_env
from trade_krono_cli.data_providers.factory import get_data_factory

# 默认值：补齐 9-12~9-14 缺失的增量数据
DEFAULT_FETCH_START = "2026-09-01"
DEFAULT_FETCH_END = "2026-10-07"
DEFAULT_BEFORE = "2026-09-14"
WORKERS = 10
BATCH_SIZE = 50
FETCH_TIMEOUT = 25
CACHE_DB = Path("outputs/cache/pipeline_cache.db")

_load_env()


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="补齐缺少最新数据的股票增量数据")
    parser.add_argument("--before", default=DEFAULT_BEFORE, help="目标最新日期阈值（缺此日期的股票被补齐）")
    parser.add_argument("--start", default=DEFAULT_FETCH_START, help="拉取窗口起始日期")
    parser.add_argument("--end", default=DEFAULT_FETCH_END, help="拉取窗口结束日期")
    return parser.parse_args()


def get_missing_tickers(before: str) -> list[str]:
    """获取缺少指定最新日期的股票。"""
    import sqlite3

    conn = sqlite3.connect(str(CACHE_DB))
    rows = conn.execute(
        "SELECT ticker, MAX(end) as latest_end FROM kline_cache "
        "GROUP BY ticker HAVING latest_end < ? ORDER BY ticker",
        (before,),
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def fetch_and_append(
    factory, ticker: str, fetch_start: str, fetch_end: str
) -> tuple[str, bool, str]:
    """拉取增量数据并追加到现有记录（不覆盖历史）。

    使用 get_provider_chain 路由：ETF 优先 baostock（原生支持 ETF K 线），
    普通股票按交易所降级链。ETF 无复权概念，缓存统一按 adjustflag='1' 存储。
    """
    try:
        providers = get_provider_chain(ticker)
        adjustflag = "1"

        result = None
        used_provider = ""
        for pname in providers:
            provider = factory.get_provider(pname)
            if provider is None:
                continue
            try:
                result = provider.fetch_kline(
                    ticker, fetch_start, fetch_end, frequency="d", adjustflag=adjustflag
                )
                if result is not None and not result.is_empty:
                    used_provider = pname
                    break
            except Exception:
                continue

        if result is None or result.is_empty:
            return (ticker, False, "empty")

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
            # 只保留严格晚于现有最后日期的行（真正的增量）
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
            adjustflag=adjustflag,
        )
        n_new = len(combined) - len(existing_df) if row else len(combined)
        return (ticker, True, f"{n_new}rows@{used_provider}")
    except Exception as e:
        return (ticker, False, str(e)[:60])


def main() -> None:
    args = parse_args()
    logger.info("=" * 60)
    logger.info(f"补齐缺失最新数据（缺 {args.before}）— tonghuashun 重试，拉取窗口 {args.start}~{args.end}")
    logger.info("=" * 60)

    tickers = get_missing_tickers(args.before)
    bj = [t for t in tickers if t.startswith("bj.")]
    shsz = [t for t in tickers if not t.startswith("bj.")]
    logger.info(f"需补齐 {len(tickers)} 只: bj={len(bj)}, sh/sz={len(shsz)}")

    if not tickers:
        logger.info("无需补齐")
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
            futures = {
                executor.submit(fetch_and_append, factory, t, args.start, args.end): t
                for t in batch
            }
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

    logger.info("\n" + "=" * 60)
    logger.info(f"完成！成功 {total_ok}，失败 {total_fail}，总计 {len(tickers)}")
    if failed_list:
        logger.warning(f"失败股票 ({len(failed_list)}): {', '.join(t for t, _ in failed_list)}")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
