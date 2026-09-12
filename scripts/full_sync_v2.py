#!/usr/bin/env python3
"""优化版全量 K 线同步脚本。

优化点：
  - 沪市股票优先使用 tonghuashun（baostock 对部分股票有异常）
  - 深市创业板优先使用 tonghuashun
  - 北交所只使用 tonghuashun
  - 减少 mootdx 使用（容易 hang）
  - 更短的超时时间（10s）
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

from trade_krono_cli.cli_commands.core import _load_env
from trade_krono_cli.data_providers.factory import get_data_factory

END_DATE = "2026-09-12"
START_DATE = "2020-01-01"
BATCH_SIZE = 200
WORKERS = 16
FETCH_TIMEOUT = 10  # 缩短超时时间
CACHE_DB = Path("outputs/cache/pipeline_cache.db")
KNOWN_TICKERS_FILE = Path("outputs/cache/known_tickers.txt")

_load_env()


def _get_saved_tickers() -> set[str]:
    """从数据库读取已保存的 ticker 集合。"""
    if not CACHE_DB.exists():
        return set()
    import sqlite3

    conn = sqlite3.connect(str(CACHE_DB))
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT ticker FROM kline_cache")
    saved = {row[0] for row in cur.fetchall()}
    conn.close()
    return saved


def _load_known_tickers() -> list[str]:
    """从本地缓存加载 A 股列表。"""
    if KNOWN_TICKERS_FILE.exists():
        tickers = []
        with open(KNOWN_TICKERS_FILE) as f:
            for line in f:
                t = line.strip()
                if t:
                    tickers.append(t)
        return sorted(set(tickers))

    from trade_krono_cli.universe.provider import TongHuaShunUniverseProvider

    provider = TongHuaShunUniverseProvider()
    tickets = provider.get_universe()
    return sorted({t.ticker for t in tickets if t.ticker})


def _get_provider_chain(ticker: str) -> list[str]:
    """根据股票类型返回 provider 优先级链。"""
    if ticker.startswith("bj."):
        # 北交所只用 tonghuashun
        return ["tonghuashun"]
    elif ticker.startswith("sh.") or ticker.startswith("sz."):
        # 沪深股票优先 tonghuashun，然后 baostock
        return ["tonghuashun", "baostock", "mootdx"]
    return ["baostock", "tonghuashun", "mootdx"]


def _fetch_with_timeout(factory, ticker: str, provider_chain: list[str]) -> tuple[str, int, str]:
    """带超时的单股票拉取。"""
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
        logger.warning(f"  ⏱️  超时 {FETCH_TIMEOUT}s: {ticker}")
        return ticker, 0, "TIMEOUT"

    if error_container:
        return ticker, 0, f"ERROR: {error_container[0]}"

    return result_container[0] if result_container else (ticker, 0, "UNKNOWN")


def main() -> None:
    logger.info("🚀 启动优化版全量 K 线同步")

    all_tickers = _load_known_tickers()
    logger.info(f"📋 共 {len(all_tickers)} 只 A 股")

    saved = _get_saved_tickers()
    if saved:
        logger.info(
            f"📌 断点续传：跳过已保存的 {len(saved)} 只，剩余 {len(all_tickers) - len(saved)} 只"
        )
        tickers = [t for t in all_tickers if t not in saved]
    else:
        tickers = all_tickers

    factory = get_data_factory()

    # 统计缺失股票分布
    bj_count = sum(1 for t in tickers if t.startswith("bj."))
    sh_count = sum(1 for t in tickers if t.startswith("sh."))
    sz_count = sum(1 for t in tickers if t.startswith("sz."))
    logger.info(f"📊 待同步分布: 北交所 {bj_count}, 沪市 {sh_count}, 深市 {sz_count}")

    total = len(tickers)
    success = 0
    failed = []
    start_time = time.time()

    for batch_start in range(0, total, BATCH_SIZE):
        batch = tickers[batch_start : batch_start + BATCH_SIZE]
        batch_num = batch_start // BATCH_SIZE + 1
        logger.info(f"📦 批次 {batch_num} [{batch_start + 1}~{batch_start + len(batch)}/{total}]")

        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = {
                pool.submit(_fetch_with_timeout, factory, t, _get_provider_chain(t)): t
                for t in batch
            }
            for future in as_completed(futures):
                ticker, rows, provider_name = future.result()
                if rows > 0:
                    success += 1
                else:
                    failed.append(ticker)
                elapsed = time.time() - start_time
                rate = (success + len(failed)) / elapsed * 60 if elapsed > 0 else 0
                logger.info(
                    f"  {'✅' if rows > 0 else '❌'} [{success + len(failed)}/{len(batch)}] "
                    f"{ticker} {rows}行 ← {provider_name or 'FAIL'} "
                    f"(总: {success + len(failed)}/{total}, 速率: {rate:.1f}/min)"
                )

        time.sleep(2)

    elapsed_total = time.time() - start_time
    logger.info(f"\n{'=' * 60}")
    logger.info("✅ 同步完成!")
    logger.info(f"  成功: {success}, 失败: {len(failed)}")
    logger.info(f"  耗时: {elapsed_total:.1f}s ({elapsed_total / 60:.1f}min)")
    logger.info(f"  速率: {success / elapsed_total * 60:.1f} 只/分钟")
    if failed:
        logger.warning(f"  失败股票 ({len(failed)}): {', '.join(failed[:20])}")
    logger.info(f"{'=' * 60}")


if __name__ == "__main__":
    main()
