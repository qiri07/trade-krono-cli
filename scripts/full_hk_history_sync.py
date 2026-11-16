#!/usr/bin/env python3
"""全量港股历史K线数据同步脚本。

从 2020-01-01 开始拉取全市场港股日线数据，写入 pipeline_cache.db 缓存。

特点：
- 获取全量港股列表（通过 akshare.stock_hk_spot()，约 2800 只）
- 逐只拉取从 2020-01-01 到最新的日线数据
- 使用 DataProviderFactory 统一限流（每秒 2 请求，最小间隔 0.5 秒）和重试
- 采用 akshare_hk Provider（主源 akshare 新浪，备用新浪直连/腾讯）
- 支持断点续传：跳过已有完整数据的股票，只补拉缺失的
- 支持 --codes 指定部分股票，--start 指定起始日期

用法：
    uv run python scripts/full_hk_history_sync.py              # 全量拉取所有港股
    uv run python scripts/full_hk_history_sync.py --codes "00700,00941"  # 指定股票
    uv run python scripts/full_hk_history_sync.py --start 2023-01-01     # 指定起始日期
    uv run python scripts/full_hk_history_sync.py --dry-run              # 仅打印计划
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger

from scripts._utils import get_cache_db_path, get_sqlite_conn
from trade_krono_cli.cache import get_cache
from trade_krono_cli.cli_commands.core import _load_env
from trade_krono_cli.data_providers.factory import get_data_factory

CACHE_DB = get_cache_db_path()
START_DATE = "2020-01-01"
WORKERS = 3  # 港股数据源较敏感，保守并发


def get_hk_ticker_list() -> list[str]:
    """通过 akshare 获取全量港股代码列表。"""
    import akshare as ak

    logger.info("📊 正在获取全量港股列表...")
    df = ak.stock_hk_spot()
    if df is None or df.empty:
        logger.error("❌ 无法获取港股列表，akshare 返回空数据")
        return []
    codes = df["代码"].astype(str).str.zfill(5).tolist()
    tickers = [f"hk.{code}" for code in codes]
    logger.info(f"✅ 获取到 {len(tickers)} 只港股")
    return tickers


def get_cached_hk_tickers() -> set[str]:
    """获取缓存中已有数据的港股 ticker。"""
    conn = get_sqlite_conn(CACHE_DB)
    rows = conn.execute(
        "SELECT ticker, MAX(end) as last_end FROM kline_cache "
        "WHERE ticker LIKE 'hk.%' GROUP BY ticker"
    ).fetchall()
    conn.close()
    return {r[0] for r in rows}


def fetch_and_store_hk(
    factory, ticker: str, start_date: str, end_date: str
) -> tuple[str, bool, str]:
    """拉取单只港股 K 线并写入缓存。

    使用 factory.fetch_kline（带限流 + 重试 + 降级），然后写入缓存。
    """
    try:
        result = factory.fetch_kline(
            ticker, start_date, end_date, frequency="d", adjustflag="1"
        )
        if result is None or result.is_empty:
            return (ticker, False, "empty")

        df = result.to_dataframe()
        if df is None or len(df) == 0:
            return (ticker, False, "empty_df")

        # 确保有 timestamps 列
        if "timestamps" not in df.columns:
            if "date" in df.columns:
                df = df.rename(columns={"date": "timestamps"})
            else:
                return (ticker, False, "no_timestamp_col")

        df["timestamps"] = pd.to_datetime(df["timestamps"])
        df = df.sort_values("timestamps").reset_index(drop=True)

        # 写入缓存
        cache = get_cache()
        cache.set_kline(
            ticker,
            df["timestamps"].iloc[0].strftime("%Y-%m-%d"),
            df["timestamps"].iloc[-1].strftime("%Y-%m-%d"),
            freq="d",
            df=df,
            ttl=0.0,
            adjustflag="1",
        )
        return (ticker, True, f"{len(df)} rows")

    except Exception as e:
        return (ticker, False, str(e)[:80])


def main() -> None:
    parser = argparse.ArgumentParser(description="全量港股历史K线数据同步")
    parser.add_argument(
        "--start",
        default=START_DATE,
        help=f"起始日期 YYYY-MM-DD（默认 {START_DATE}）",
    )
    parser.add_argument(
        "--end",
        default=None,
        help="结束日期 YYYY-MM-DD（默认今天）",
    )
    parser.add_argument(
        "--codes",
        default=None,
        help="指定港股代码，逗号分隔，如 00700,00941（不含 hk. 前缀）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅打印计划，不实际拉取",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="强制重新拉取所有股票，即使已有缓存数据",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=WORKERS,
        help=f"并发线程数（默认 {WORKERS}）",
    )
    args = parser.parse_args()

    _load_env()

    start_date = args.start
    end_date = args.end or datetime.now().strftime("%Y-%m-%d")
    logger.info(
        f"🚀 全量港股历史数据同步启动  {start_date} ~ {end_date}"
    )
    logger.info(f"  并发线程数: {args.workers}")

    # 获取股票列表
    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
        tickers = [f"hk.{c.zfill(5)}" for c in codes]
        logger.info(f"  指定股票: {len(tickers)} 只")
    else:
        tickers = get_hk_ticker_list()
        if not tickers:
            logger.error("❌ 无法获取港股列表，退出")
            sys.exit(1)

    if args.dry_run:
        logger.info("🧪 DRY-RUN 模式：仅打印计划，不实际拉取")
        for t in tickers:
            print(f"  {t}")
        return

    # 检查已缓存的股票
    cached_tickers = get_cached_hk_tickers()
    if cached_tickers:
        already_cached = [t for t in tickers if t in cached_tickers]
        need_fetch = [t for t in tickers if t not in cached_tickers]
        if args.force:
            need_fetch = tickers
            logger.info(
                f"  --force 模式：忽略缓存，全部 {len(need_fetch)} 只重新拉取"
            )
        else:
            logger.info(
                f"  已缓存: {len(already_cached)} 只，需要拉取: {len(need_fetch)} 只"
            )
    else:
        need_fetch = tickers
        logger.info(f"  无缓存数据，需要拉取: {len(need_fetch)} 只")

    if not need_fetch:
        logger.info("✅ 所有股票已有数据，无需拉取")
        return

    # 初始化 factory
    factory = get_data_factory()

    # 确保 akshare_hk provider 可用
    hk_provider = factory.get_provider("akshare_hk")
    if hk_provider is None:
        logger.error("❌ akshare_hk provider 不可用，退出")
        sys.exit(1)
    logger.info("✅ akshare_hk provider 已就绪")

    # 逐批拉取
    total_ok = 0
    total_fail = 0
    batch_size = 50

    for batch_start in range(0, len(need_fetch), batch_size):
        batch = need_fetch[batch_start : batch_start + batch_size]
        logger.info(
            f"📦 批次 [{batch_start + 1}~{batch_start + len(batch)}/{len(need_fetch)}]"
        )

        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {
                executor.submit(
                    fetch_and_store_hk, factory, t, start_date, end_date
                ): t
                for t in batch
            }
            for future in as_completed(futures):
                ticker = futures[future]
                try:
                    ok, success, detail = future.result()
                    if success:
                        total_ok += 1
                        logger.debug(f"  ✅ {ticker}: {detail}")
                    else:
                        total_fail += 1
                        logger.warning(f"  ❌ {ticker}: {detail}")
                except Exception as e:
                    total_fail += 1
                    logger.warning(f"  ❌ {ticker}: 异常 {e}")

        # 批次间短暂休息，避免触发限流
        if batch_start + batch_size < len(need_fetch):
            time.sleep(2)

    logger.info("=" * 60)
    logger.info(f"✅ 全量港股同步完成！成功 {total_ok}，失败 {total_fail}，总计 {len(need_fetch)}")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
