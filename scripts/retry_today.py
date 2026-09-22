#!/usr/bin/env python3
"""重试同步今日增量数据 — 仅针对真正缺失今日数据的股票。"""

from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
from loguru import logger

from trade_krono_cli.cache import get_cache
from trade_krono_cli.cli_commands.core import _load_env
from trade_krono_cli.data_providers.factory import get_data_factory

TODAY = date.today().isoformat()  # 动态取今日，避免硬编码
WORKERS = 16
FETCH_TIMEOUT = 30
BATCH_DELAY = 2.0  # 每批之间的延迟（秒），避免 API 限流
CACHE_DB = Path("outputs/cache/pipeline_cache.db")

_load_env()


def get_missing_tickers() -> list[str]:
    """获取真正缺失今日数据的非北交所股票（用 NOT EXISTS 避免误判）。"""
    import sqlite3

    conn = sqlite3.connect(str(CACHE_DB))
    rows = conn.execute(
        """
        SELECT DISTINCT k.ticker FROM kline_cache k
        WHERE k.ticker NOT LIKE 'bj.%'
          AND NOT EXISTS (
              SELECT 1 FROM kline_cache k2
              WHERE k2.ticker = k.ticker AND k2.start >= ?
          )
        ORDER BY k.ticker
        """,
        (TODAY,),
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def fetch_today(factory, ticker: str) -> tuple[str, bool, str]:
    """拉取今日增量数据并追加。"""
    try:
        if ticker.startswith("bj."):
            providers = ["tonghuashun"]
        else:
            providers = ["tonghuashun", "baostock"]

        cache = get_cache()
        for pname in providers:
            provider = factory.get_provider(pname)
            if provider is None:
                continue
            try:
                result = provider.fetch_kline(ticker, TODAY, TODAY, frequency="d", adjustflag="1")
                if result is None or result.is_empty:
                    continue
                df = result.to_dataframe()
                if len(df) == 0:
                    continue
                # 检查是否已存在今日数据
                cur = cache.get_kline(ticker, TODAY, TODAY, "d", "1")
                if cur is not None and len(cur) > 0:
                    combined = pd.concat([cur, df], ignore_index=True)
                    combined = combined.drop_duplicates(subset=["timestamps"], keep="last")
                    combined = combined.sort_values("timestamps").reset_index(drop=True)
                    cache.set_kline(ticker, TODAY, TODAY, "d", combined, ttl=0.0)
                else:
                    cache.set_kline(ticker, TODAY, TODAY, "d", df, ttl=0.0)
                return (ticker, True, f"{pname}:{len(df)}条")
            except Exception as e:
                logger.opt(exception=True).debug(f"{ticker} {pname} 异常: {e}")
                continue
        return (ticker, False, "all_failed")
    except Exception as e:
        return (ticker, False, f"error:{e}")


def main() -> None:
    factory = get_data_factory()
    tickers = get_missing_tickers()
    if not tickers:
        logger.info("✅ 所有股票已有今日数据，无需同步")
        return

    logger.info(f"📌 需要同步今日({TODAY})增量的股票: {len(tickers)} 只")

    batch_size = 64
    all_results: list[tuple[str, bool, str]] = []
    total = len(tickers)

    for batch_start in range(0, total, batch_size):
        batch = tickers[batch_start : batch_start + batch_size]
        batch_num = batch_start // batch_size + 1
        logger.info(f"  批次 {batch_num} ({batch_start + 1}~{min(batch_start + batch_size, total)}/{total})")

        results: list[tuple[str, bool, str]] = []
        with ThreadPoolExecutor(max_workers=WORKERS) as executor:
            futures = {executor.submit(fetch_today, factory, t): t for t in batch}
            for future in as_completed(futures, timeout=FETCH_TIMEOUT * len(batch) // WORKERS + 30):
                try:
                    results.append(future.result(timeout=FETCH_TIMEOUT))
                except Exception as e:
                    results.append((futures[future], False, f"timeout:{e}"))

        all_results.extend(results)
        success_count = sum(1 for r in results if r[1])
        logger.info(f"    本批: {success_count}/{len(batch)} 成功")

        # 批次间短暂休息，避免 API 限流
        if batch_start + batch_size < total:
            time.sleep(BATCH_DELAY)

    success = [r for r in all_results if r[1]]
    failed = [r for r in all_results if not r[1]]

    logger.info("\n📌 今日增量同步结果:")
    logger.info(f"   成功: {len(success)}/{total}")
    if failed:
        logger.warning(f"   失败: {len(failed)}/{total}")
        logger.warning(f"   仍失败: {[r[0] for r in failed]}")

    # 最终统计
    import sqlite3

    conn = sqlite3.connect(str(CACHE_DB))
    total_stocks = conn.execute(
        "SELECT COUNT(DISTINCT ticker) FROM kline_cache WHERE ticker NOT LIKE 'bj.%'"
    ).fetchone()[0]
    today_cnt = conn.execute(
        "SELECT COUNT(DISTINCT ticker) FROM kline_cache WHERE start >= ?", (TODAY,)
    ).fetchone()[0]
    conn.close()
    logger.info("\n📊 最终统计:")
    logger.info(f"   非北交所总股票数: {total_stocks}")
    logger.info(f"   今日({TODAY})数据: {today_cnt}/{total_stocks} ({today_cnt * 100 / total_stocks:.1f}%)")
    logger.info("✅ 完成")


if __name__ == "__main__":
    main()
