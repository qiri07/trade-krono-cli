#!/usr/bin/env python3
"""将 K 线历史回退至 2015-01-01 的全量同步脚本。

对现有缓存中 start >= 2020 的股票，重新拉取 2015-01-01 起的历史数据，
与现有记录合并（保留已有部分），实现数据范围扩展。

用法：
  uv run python scripts/full_history_from_2015.py
  uv run python scripts/full_history_from_2015.py --start 2015-01-01 --end 2026-09-15
"""

from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
from loguru import logger

from scripts._utils import get_all_tickers, get_provider_chain
from trade_krono_cli.cache import get_cache
from trade_krono_cli.cli_commands.core import _load_env
from trade_krono_cli.data_providers.factory import get_data_factory

START_DATE = "2015-01-01"
END_DATE = "2026-09-15"
BATCH_SIZE = 100
WORKERS = 16
FETCH_TIMEOUT = 20
CACHE_DB = Path("outputs/cache/pipeline_cache.db")

_load_env()


def _fetch_and_append(
    factory, ticker: str, provider_chain: list[str], start_date: str, end_date: str, cache_db: Path
) -> tuple[str, int, str]:
    """拉取历史数据并追加到现有记录（不覆盖已有数据）。"""
    try:
        df_new = None
        for provider_name in provider_chain:
            try:
                provider = factory.get_provider(provider_name)
                if provider is None:
                    continue
                result = provider.fetch_kline(
                    ticker, start_date, end_date, frequency="d", adjustflag="1"
                )
                if result is None or result.is_empty:
                    continue
                df = result.to_dataframe()
                if df is None or len(df) == 0:
                    continue
                df_new = df
                break
            except Exception as e:
                logger.debug(f"  {provider_name} 失败 {ticker}: {e}")

        if df_new is None:
            return (ticker, 0, "FAIL")

        ts_col = "timestamps" if "timestamps" in df_new.columns else "date"
        ts = pd.to_datetime(df_new[ts_col])

        import sqlite3

        conn = sqlite3.connect(str(cache_db))
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

            # 只保留早于现有记录的行（补齐历史部分）
            cutoff = pd.Timestamp(existing_start)
            old_rows = df_new[ts < cutoff]
            if len(old_rows) == 0:
                return (ticker, 0, "already_full")
            combined = pd.concat([old_rows, existing_df], ignore_index=True)
            combined = combined.sort_values(ts_col).reset_index(drop=True)
        else:
            combined = df_new

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
        return (ticker, n_new, f"{n_new}rows")
    except Exception as e:
        return (ticker, 0, f"ERROR: {str(e)[:60]}")


def main(start_date: str, end_date: str) -> None:
    """执行历史数据回退同步，将 K 线数据扩展至指定起始日期。

    Args:
        start_date: 目标起始日期，格式 YYYY-MM-DD。
        end_date: 目标结束日期，格式 YYYY-MM-DD。
    """
    logger.info(f"🚀 启动历史回退同步：{start_date} ~ {end_date}")

    all_tickers = get_all_tickers()
    logger.info(f"📋 共 {len(all_tickers)} 只股票待处理")

    # 先检查哪些股票需要补全
    import sqlite3

    conn = sqlite3.connect(str(CACHE_DB))
    need_update = [
        r[0]
        for r in conn.execute(
            "SELECT ticker FROM kline_cache WHERE start >= ?", (START_DATE,)
        ).fetchall()
    ]
    already_full = [
        r[0]
        for r in conn.execute(
            "SELECT ticker FROM kline_cache WHERE start < ?", (START_DATE,)
        ).fetchall()
    ]
    conn.close()

    if already_full:
        logger.info(f"📌 已有完整历史数据（{len(already_full)} 只），跳过")
    if not need_update:
        logger.info("✅ 所有股票历史已覆盖起始日期，无需补全")
        return

    logger.info(f"📌 需补全历史：{len(need_update)} 只")

    factory = get_data_factory()
    success = 0
    failed = []
    start_time = time.time()

    for batch_start in range(0, len(need_update), BATCH_SIZE):
        batch = need_update[batch_start : batch_start + BATCH_SIZE]
        batch_num = batch_start // BATCH_SIZE + 1
        logger.info(
            f"📦 批次 {batch_num} [{batch_start + 1}~{batch_start + len(batch)}/{len(need_update)}]"
        )

        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = {
                pool.submit(
                    _fetch_and_append,
                    factory,
                    t,
                    get_provider_chain(t),
                    start_date,
                    end_date,
                    CACHE_DB,
                ): t
                for t in batch
            }
            for future in as_completed(futures):
                ticker, n_new, status = future.result()
                if n_new > 0:
                    success += 1
                    logger.info(f"  ✅ {ticker} +{n_new}行 ({status})")
                else:
                    failed.append(ticker)
                    logger.warning(f"  ❌ {ticker} ({status})")

        time.sleep(1)

    elapsed = time.time() - start_time
    logger.info(f"\n{'=' * 60}")
    logger.info("✅ 历史回退同步完成!")
    logger.info(f"  补全: {success}, 无需补全/失败: {len(failed)}")
    logger.info(f"  耗时: {elapsed:.1f}s ({elapsed / 60:.1f}min)")
    if failed:
        logger.warning(f"  失败股票 ({len(failed)}): {', '.join(failed[:20])}")
    logger.info(f"{'=' * 60}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="将 K 线历史回退至指定起始日期")
    parser.add_argument("--start", default=START_DATE, help="起始日期 (YYYY-MM-DD)")
    parser.add_argument("--end", default=END_DATE, help="结束日期 (YYYY-MM-DD)")
    args = parser.parse_args()

    main(start_date=args.start, end_date=args.end)
