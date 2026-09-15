#!/usr/bin/env python3
"""批量拉取股票元数据（参考数据）。

通过同花顺 API 获取所有股票的名称、交易所后缀等基础信息，
写入 outputs/cache/metadata.json，供分析脚本使用。

用法：
  uv run python scripts/fetch_reference_data.py
  uv run python scripts/fetch_reference_data.py --tickers "000001,002027"
"""

from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger

from trade_krono_cli.cli_commands.core import _load_env
from trade_krono_cli.data_providers.factory import get_data_factory

WORKERS = 20
BATCH_SIZE = 100
FETCH_TIMEOUT = 15
CACHE_DB = Path("outputs/cache/pipeline_cache.db")
METADATA_FILE = Path("outputs/cache/metadata.json")

_load_env()


def _get_all_tickers() -> list[str]:
    """从数据库读取所有 ticker。"""
    import sqlite3

    conn = sqlite3.connect(str(CACHE_DB))
    tickers = [r[0] for r in conn.execute("SELECT DISTINCT ticker FROM kline_cache").fetchall()]
    conn.close()
    return sorted(tickers)


def _fetch_metadata_one(factory, ticker: str) -> tuple[str, dict | None, str]:
    """拉取单只股票的元数据。"""
    try:
        meta = factory.fetch_metadata(ticker)
        if meta is None:
            return (ticker, None, "none")
        return (
            ticker,
            {
                "ticker": meta.ticker,
                "industry": meta.industry,
                "pe_ttm": meta.pe_ttm,
                "pb": meta.pb,
                "ipo_date": meta.ipo_date,
                "is_st": meta.is_st,
                "source": meta.source,
            },
            "ok",
        )
    except Exception as e:
        return (ticker, None, f"error: {str(e)[:50]}")


def main(tickers_input: list[str] | None = None) -> None:
    logger.info("📊 启动参考数据（元数据）拉取")

    if tickers_input:
        tickers = tickers_input
    else:
        tickers = _get_all_tickers()

    logger.info(f"📋 共 {len(tickers)} 只股票待获取元数据")

    factory = get_data_factory()
    results: dict[str, dict | None] = {}
    success = 0
    failed = 0
    start_time = time.time()

    for batch_start in range(0, len(tickers), BATCH_SIZE):
        batch = tickers[batch_start : batch_start + BATCH_SIZE]
        batch_num = batch_start // BATCH_SIZE + 1
        logger.info(
            f"📦 批次 {batch_num} [{batch_start + 1}~{batch_start + len(batch)}/{len(tickers)}]"
        )

        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = {pool.submit(_fetch_metadata_one, factory, t): t for t in batch}
            for future in as_completed(futures):
                ticker, meta, status = future.result()
                results[ticker] = meta
                if status == "ok":
                    success += 1
                else:
                    failed += 1

        time.sleep(1)

    elapsed = time.time() - start_time
    logger.info(f"\n{'=' * 60}")
    logger.info("✅ 参考数据拉取完成!")
    logger.info(f"  成功: {success}, 失败: {failed}")
    logger.info(f"  耗时: {elapsed:.1f}s ({elapsed / 60:.1f}min)")

    # 保存元数据到 JSON
    METADATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    METADATA_FILE.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"  已保存至: {METADATA_FILE}")
    logger.info(f"{'=' * 60}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="批量拉取股票元数据（参考数据）")
    parser.add_argument("--tickers", default=None, help="股票代码（逗号分隔），空=全量")
    args = parser.parse_args()

    tickers_list = (
        [t.strip() for t in args.tickers.split(",") if t.strip()] if args.tickers else None
    )
    main(tickers_input=tickers_list)
