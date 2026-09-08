#!/usr/bin/env python3
"""对滞后的个股执行增量 K 线补拉。"""

from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from loguru import logger

from scripts.check_and_sync_cache import get_expected_date
from trade_krono_cli.cli_commands.core import _load_env
from trade_krono_cli.data import fetch_kline_incremental

_WORKERS = 16


def main() -> int:
    _load_env()

    end_date = get_expected_date()

    stale_file = sys.argv[1] if len(sys.argv) > 1 else "/tmp/tickers_stale.txt"
    with open(stale_file) as f:
        tickers = [line.strip() for line in f if line.strip()]

    logger.info(f"🔄 开始增量补拉 {len(tickers)} 只滞后股票 → {end_date}")
    t0 = time.time()
    success, fail = 0, 0

    def _sync(ticker: str) -> tuple[str, int]:
        try:
            df = fetch_kline_incremental(
                ticker, "2022-08-31", end_date, frequency="d", adjustflag="1", use_cache=True
            )
            return (ticker, len(df)) if df is not None and len(df) > 0 else (ticker, 0)
        except Exception as e:
            logger.debug(f"  {ticker} 失败: {e}")
            return (ticker, 0)

    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        futures = {pool.submit(_sync, t): t for t in tickers}
        for i, future in enumerate(as_completed(futures), 1):
            ticker, rows = future.result()
            if rows > 0:
                success += 1
            else:
                fail += 1
            if i % 500 == 0 or i == len(tickers):
                logger.info(
                    f"  进度 {i}/{len(tickers)}  成功={success}  失败={fail}  耗时={time.time() - t0:.0f}s"
                )

    elapsed = time.time() - t0
    logger.info(f"✅ 增量补拉完成: 成功={success}  失败={fail}  总耗时={elapsed:.0f}s")
    return 0 if fail < len(tickers) * 0.1 else 1


if __name__ == "__main__":
    sys.exit(main())
