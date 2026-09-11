#!/usr/bin/env python3
"""全量历史数据同步脚本 — 从2020-01-01开始重新拉取所有股票。

用途：确保所有股票都有完整的2020年至今的历史数据。
"""

from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Thread

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
from loguru import logger

from trade_krono_cli.cache import get_cache
from trade_krono_cli.cli_commands.core import _load_env
from trade_krono_cli.data_providers.factory import get_data_factory

START_DATE = "2020-01-01"
END_DATE = "2026-09-10"
BATCH_SIZE = 100
WORKERS = 16
FETCH_TIMEOUT = 20
CACHE_DB = Path("outputs/cache/pipeline_cache.db")

_load_env()


def _get_all_tickers() -> list[str]:
    """获取所有需要同步的股票列表。"""
    import sqlite3

    conn = sqlite3.connect(str(CACHE_DB))
    cur = conn.cursor()
    # 获取所有ticker
    cur.execute("SELECT DISTINCT ticker FROM kline_cache")
    tickers = [r[0] for r in cur.fetchall()]
    conn.close()
    return sorted(tickers)


def _get_provider_chain(ticker: str) -> list[str]:
    """根据股票类型返回 provider 优先级链。"""
    if ticker.startswith("bj."):
        return ["tonghuashun"]
    elif ticker.startswith("sh.") or ticker.startswith("sz."):
        return ["tonghuashun", "baostock"]
    return ["baostock", "tonghuashun"]


def _fetch_full_range(factory, ticker: str, provider_chain: list[str]) -> tuple[str, int, str]:
    """带超时的全量历史数据拉取。"""
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
                    cache = get_cache()
                    ts = pd.to_datetime(df["timestamps"])
                    cache.set_kline(
                        ticker,
                        ts.min().strftime("%Y-%m-%d"),
                        ts.max().strftime("%Y-%m-%d"),
                        "d",
                        df,
                        ttl=0.0,
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
        logger.warning(f"  ⏱️  超时 {FETCH_TIMEOUT}s: {ticker}")
        return ticker, 0, "TIMEOUT"

    if error_container:
        return ticker, 0, f"ERROR: {error_container[0]}"

    return result_container[0] if result_container else (ticker, 0, "UNKNOWN")


def main() -> None:
    logger.info(f"🚀 启动全量历史数据同步（{START_DATE} ~ {END_DATE}）")

    all_tickers = _get_all_tickers()
    logger.info(f"📋 共 {len(all_tickers)} 只 A 股")

    factory = get_data_factory()

    total = len(all_tickers)
    success = 0
    failed = []
    start_time = time.time()

    for batch_start in range(0, total, BATCH_SIZE):
        batch = all_tickers[batch_start : batch_start + BATCH_SIZE]
        batch_num = batch_start // BATCH_SIZE + 1
        logger.info(f"📦 批次 {batch_num} [{batch_start + 1}~{batch_start + len(batch)}/{total}]")

        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = {
                pool.submit(_fetch_full_range, factory, t, _get_provider_chain(t)): t for t in batch
            }
            for future in as_completed(futures):
                ticker, rows, provider_name = future.result()
                if rows > 0:
                    success += 1
                    if success % 100 == 0:
                        elapsed = time.time() - start_time
                        rate = success / elapsed * 3600 if elapsed > 0 else 0
                        eta = (total - success) / rate if rate > 0 else 0
                        logger.info(
                            f"  进度: {success}/{total} ({success * 100 / total:.1f}%) | "
                            f"速度: {rate:.0f}只/小时 | ETA: {eta / 60:.1f}分钟"
                        )
                else:
                    failed.append((ticker, provider_name))

    elapsed = time.time() - start_time
    logger.info(f"\n{'=' * 60}")
    logger.info("✅ 全量同步完成！")
    logger.info(f"   成功: {success}/{total} ({success * 100 / total:.1f}%)")
    logger.info(f"   失败: {len(failed)}")
    logger.info(f"   耗时: {elapsed / 60:.1f} 分钟")

    if failed:
        logger.info("\n❌ 失败股票（前20只）:")
        for t, p in failed[:20]:
            logger.info(f"   {t}: {p}")

    # 最终统计
    import sqlite3

    conn = sqlite3.connect(str(CACHE_DB))
    cur = conn.cursor()
    total_records = cur.execute("SELECT COUNT(*) FROM kline_cache").fetchone()[0]
    distinct_tickers = cur.execute("SELECT COUNT(DISTINCT ticker) FROM kline_cache").fetchone()[0]
    latest_date = cur.execute("SELECT MAX(end) FROM kline_cache").fetchone()[0]
    since_2020 = cur.execute(
        "SELECT COUNT(DISTINCT ticker) FROM kline_cache WHERE start >= '2020-01-01'"
    ).fetchone()[0]
    conn.close()

    logger.info("\n📊 最终状态:")
    logger.info(f"   总记录: {total_records:,} 条")
    logger.info(f"   股票数: {distinct_tickers:,} 只")
    logger.info(f"   最新日期: {latest_date}")
    logger.info(f"   2020年数据: {since_2020}只 ({since_2020 * 100 / distinct_tickers:.1f}%)")


if __name__ == "__main__":
    main()
