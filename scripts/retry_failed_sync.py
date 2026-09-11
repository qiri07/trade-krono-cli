#!/usr/bin/env python3
"""重试失败股票同步脚本。

针对全量同步中失败的股票进行重试，采用以下策略：
  - 使用 factory.fetch_kline() 自动降级多个 provider
  - 北交所只使用 tonghuashun（通过 factory 内部逻辑）
  - 增加重试次数（最多3次）
  - 使用 concurrent.futures 添加超时保护（5s）避免 hang
"""

from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeoutError
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger

from trade_krono_cli.cli_commands.core import _load_env
from trade_krono_cli.data_providers.factory import get_data_factory

END_DATE = "2026-09-09"
START_DATE = "2022-01-01"
MAX_RETRIES = 3
FETCH_TIMEOUT = 5  # 每次 fetch_kline 调用的超时秒数
MISSING_FILE = Path("outputs/cache/missing_tickers.txt")
CACHE_DB = Path("outputs/cache/pipeline_cache.db")

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


def _fetch_with_timeout(factory, ticker: str) -> tuple[str, int, str]:
    """带超时的单股票拉取，使用 factory 自动降级 provider。"""
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(factory.fetch_kline, ticker, START_DATE, END_DATE, "d", "1")
        try:
            result = future.result(timeout=FETCH_TIMEOUT)
        except FuturesTimeoutError:
            future.cancel()
            return ticker, 0, "TIMEOUT"
        except Exception as e:
            logger.debug(f"  {ticker} 拉取异常: {e}")
            return ticker, 0, "ERROR"

    if result is None or result.is_empty:
        return ticker, 0, "EMPTY"

    df = result.to_dataframe()
    if len(df) == 0:
        return ticker, 0, "EMPTY"

    return ticker, len(df), "factory"


def _fetch_with_retry(factory, ticker: str) -> tuple[str, int, str]:
    """带多次重试的单股票拉取。"""
    for attempt in range(1, MAX_RETRIES + 1):
        ticker_out, rows, source = _fetch_with_timeout(factory, ticker)
        if rows > 0:
            return ticker_out, rows, f"{source}(retry={attempt})"
    return ticker, 0, "FAIL"


def main() -> None:
    logger.info("🔄 启动失败股票重试同步")

    # 读取失败列表
    if not MISSING_FILE.exists():
        logger.error(f"失败列表文件不存在: {MISSING_FILE}")
        sys.exit(1)

    with open(MISSING_FILE) as f:
        tickers = [line.strip() for line in f if line.strip()]

    logger.info(f"📋 共 {len(tickers)} 只失败股票待重试")

    # 过滤已保存的
    saved = _get_saved_tickers()
    tickers = [t for t in tickers if t not in saved]
    logger.info(f"📌 实际待处理: {len(tickers)} 只（已排除 {len(saved)} 只）")

    factory = get_data_factory()

    # 统计分布
    bj_count = sum(1 for t in tickers if t.startswith("bj."))
    sh688_count = sum(1 for t in tickers if t.startswith("sh.688"))
    sz300_count = sum(1 for t in tickers if t.startswith("sz.300") or t.startswith("sz.301"))
    logger.info(f"📊 分布: 北交所 {bj_count}, 科创板 {sh688_count}, 创业板 {sz300_count}")

    total = len(tickers)
    success = 0
    failed = []
    start_time = time.time()

    # 分批处理
    BATCH_SIZE = 50
    for batch_start in range(0, total, BATCH_SIZE):
        batch = tickers[batch_start : batch_start + BATCH_SIZE]
        batch_num = batch_start // BATCH_SIZE + 1
        logger.info(f"📦 批次 {batch_num} [{batch_start + 1}~{batch_start + len(batch)}/{total}]")

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = {
                pool.submit(_fetch_with_retry, factory, t): t for t in batch
            }
            for future in as_completed(futures):
                ticker, rows, provider_name = future.result()
                if rows > 0:
                    success += 1
                else:
                    failed.append(ticker)
                elapsed = time.time() - start_time
                rate = (success + len(failed)) / elapsed * 60 if elapsed > 0 else 0
                if rows > 0:
                    logger.info(
                        f"  ✅ [{success + len(failed)}/{len(batch)}] "
                        f"{ticker} {rows}行 ← {provider_name} "
                        f"(总: {success + len(failed)}/{total}, 速率: {rate:.1f}/min)"
                    )

        time.sleep(1)

    elapsed_total = time.time() - start_time
    logger.info(f"\n{'=' * 60}")
    logger.info("✅ 重试完成!")
    logger.info(f"  成功: {success}, 仍失败: {len(failed)}")
    logger.info(f"  耗时: {elapsed_total:.1f}s ({elapsed_total / 60:.1f}min)")
    logger.info(f"  成功率: {success / total * 100:.1f}%" if total > 0 else "  成功率: N/A")
    if failed:
        logger.warning(f"  仍失败股票 ({len(failed)}): {', '.join(failed[:30])}")
    logger.info(f"{'=' * 60}")


if __name__ == "__main__":
    main()
