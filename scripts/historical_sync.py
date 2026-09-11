#!/usr/bin/env python3
"""历史数据增量同步脚本 — 从指定起始日期补拉历史K线。

策略：
  - 只对已缓存但起始日期晚于目标日期的股票执行全量重拉
  - 使用多Provider优先级链（tonghuashun > baostock）
  - 16并发，带超时保护
"""

from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Thread

import pandas as pd
from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trade_krono_cli.cli_commands.core import _load_env
from trade_krono_cli.data_providers.factory import get_data_factory

START_DATE = "2020-01-01"
END_DATE = "2026-09-09"
BATCH_SIZE = 200
WORKERS = 16
FETCH_TIMEOUT = 15
CACHE_DB = Path("outputs/cache/pipeline_cache.db")
KNOWN_TICKERS_FILE = Path("outputs/cache/known_tickers.txt")

_load_env()


def _get_cached_data() -> list[tuple[str, str, str]]:
    """获取缓存中需要补齐的股票列表（起始日期晚于 START_DATE）。"""
    if not CACHE_DB.exists():
        return []
    import sqlite3
    conn = sqlite3.connect(str(CACHE_DB))
    cur = conn.cursor()
    # 找出 start > START_DATE 的股票，需要补全历史
    cur.execute(
        "SELECT ticker, start, end FROM kline_cache WHERE start > ?",
        (START_DATE,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def _get_provider_chain(ticker: str) -> list[str]:
    """根据股票类型返回 provider 优先级链。"""
    if ticker.startswith("bj."):
        return ["tonghuashun", "baostock"]
    elif ticker.startswith("sh.") or ticker.startswith("sz."):
        return ["tonghuashun", "baostock"]
    return ["baostock", "tonghuashun"]


def _fetch_full_range(
    factory, ticker: str, provider_chain: list[str]
) -> tuple[str, int, str]:
    """带超时的全量历史拉取。"""
    result_container: list[tuple[str, int, str]] = []
    error_container: list[Exception] = []

    def _fetch():
        try:
            for provider_name in provider_chain:
                try:
                    provider = factory.get_provider(provider_name)
                    if provider is None:
                        continue
                    result = provider.fetch_kline(
                        ticker, START_DATE, END_DATE, frequency="d", adjustflag="1"
                    )
                    if result is None or result.is_empty:
                        continue
                    df = result.to_dataframe()
                    if len(df) == 0:
                        continue
                    from trade_krono_cli.cache import get_cache
                    cache = get_cache()
                    ts = pd.to_datetime(df["timestamps"])
                    cache.set_kline(
                        ticker,
                        ts.min().strftime("%Y-%m-%d"),
                        ts.max().strftime("%Y-%m-%d"),
                        "d",
                        df,
                        ttl=86400 * 365 * 10,
                    )
                    result_container.append((ticker, len(df), provider_name))
                    return
                except Exception as e:
                    logger.debug(f"  {provider_name} 失败 {ticker}: {e}")
            result_container.append((ticker, 0, "FAIL"))
        except Exception as e:
            error_container.append(e)
            result_container.append((ticker, 0, "ERROR"))

    thread = Thread(target=_fetch)
    thread.daemon = True
    thread.start()
    thread.join(timeout=FETCH_TIMEOUT)

    if thread.is_alive():
        return ticker, 0, "TIMEOUT"
    if error_container:
        return ticker, 0, f"ERROR: {error_container[0]}"
    return result_container[0] if result_container else (ticker, 0, "UNKNOWN")


def main() -> None:
    logger.info(f"🚀 启动历史数据补齐（起始日期: {START_DATE}）")

    # 获取需要补齐的股票
    rows = _get_cached_data()
    if not rows:
        logger.info("✅ 所有股票历史数据已完整，无需补齐")
        return

    logger.info(f"📋 需要补齐历史数据的股票: {len(rows)} 只")
    for ticker, start, end in rows[:10]:
        logger.info(f"   - {ticker}: {start} ~ {end}")
    if len(rows) > 10:
        logger.info(f"   ... 还有 {len(rows) - 10} 只")

    factory = get_data_factory()

    # 准备同步列表
    tickers_to_sync = [(t, s, e) for t, s, e in rows]
    total = len(tickers_to_sync)
    success = 0
    failed = []
    start_time = time.time()

    for batch_start in range(0, total, BATCH_SIZE):
        batch = tickers_to_sync[batch_start : batch_start + BATCH_SIZE]
        batch_num = batch_start // BATCH_SIZE + 1
        logger.info(f"📦 批次 {batch_num} [{batch_start + 1}~{batch_start + len(batch)}/{total}]")

        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = {
                pool.submit(_fetch_full_range, factory, t, _get_provider_chain(t)): (t, s, e)
                for t, s, e in batch
            }
            for future in as_completed(futures):
                ticker, rows_count, provider_name = future.result()
                if rows_count > 0:
                    success += 1
                else:
                    failed.append(ticker)
                elapsed = time.time() - start_time
                rate = (success + len(failed)) / elapsed * 60 if elapsed > 0 else 0
                logger.info(
                    f"  {'✅' if rows_count > 0 else '❌'} [{success + len(failed)}/{len(batch)}] "
                    f"{ticker} {rows_count}行 ← {provider_name or 'FAIL'} "
                    f"(总: {success + len(failed)}/{total}, 速率: {rate:.1f}/min)"
                )

        time.sleep(2)

    elapsed_total = time.time() - start_time
    logger.info(f"\n{'=' * 60}")
    logger.info("✅ 历史数据补齐完成!")
    logger.info(f"  成功: {success}, 失败: {len(failed)}")
    logger.info(f"  耗时: {elapsed_total:.1f}s ({elapsed_total / 60:.1f}min)")
    logger.info(f"  速率: {success / elapsed_total * 60:.1f} 只/分钟")
    if failed:
        logger.warning(f"  失败股票 ({len(failed)}): {', '.join(failed[:20])}")
    logger.info(f"{'=' * 60}")


if __name__ == "__main__":
    main()
