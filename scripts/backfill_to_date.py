#!/usr/bin/env python3
"""把 K 线缓存中滞后于目标日期的标的增量补齐到指定日期。

与 ``fill_missing_and_today.py`` 的区别：本脚本的结束日**显式可控**（默认取
最近一个已收盘交易日，即昨天/周五），不会把盘中未收盘的部分日线写进缓存。

用法：
  uv run python scripts/backfill_to_date.py                  # 补齐到最近交易日
  uv run python scripts/backfill_to_date.py --dry-run        # 只统计不写库
  uv run python scripts/backfill_to_date.py --end-date 2026-11-02
  uv run python scripts/backfill_to_date.py --tickers "sh.510300,bj.920000"
  uv run python scripts/backfill_to_date.py --workers 8 --limit 20

输出：Rich 风格的日志统计（成功 / 无新数据 / 失败 / 耗时）。
"""

from __future__ import annotations

import argparse
import pickle
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
from loguru import logger

from scripts._utils import (
    _get_expected_date,
    get_cache_db_path,
    get_provider_chain,
    get_sqlite_conn,
    normalize_ticker_key,
)
from trade_krono_cli.cache import get_cache
from trade_krono_cli.cli_commands.core import _load_env
from trade_krono_cli.data_providers.base import DataProvider
from trade_krono_cli.data_providers.factory import DataProviderFactory, get_data_factory

# 增量回看窗口：即便缓存已到 yesterday，也允许重拉最近 N 天以修正数据缺口
LOOKBACK_DAYS = 10
# 单只标的 provider 调用超时（秒），超时后降级尝试下一个 provider
FETCH_TIMEOUT = 60.0

_load_env()


def find_stale_tickers(db_path: Path, end_date: str) -> list[tuple[str, str, str]]:
    """查询 K 线缓存中 end 早于目标日期的标的。

    Args:
        db_path: pipeline_cache.db 路径。
        end_date: 目标结束日期（YYYY-MM-DD）。

    Returns:
        [(ticker, start, end), ...]，按 ticker 升序。
    """
    conn = get_sqlite_conn(db_path)
    try:
        rows = conn.execute(
            "SELECT ticker, start, end FROM kline_cache WHERE end < ? ORDER BY ticker",
            (end_date,),
        ).fetchall()
    finally:
        conn.close()
    return [(str(r[0]), str(r[1]), str(r[2])) for r in rows]


def _fetch_one_provider(
    provider: DataProvider, ticker: str, start: str, end: str
) -> pd.DataFrame | None:
    """用单个 provider 拉取区间 K 线，返回 DataFrame（失败/空返回 None）。

    在独立线程中调用并限制总耗时，避免某个 provider 卡死拖垮整批。

    Args:
        provider: 已实例化的数据源。
        ticker: 带前缀 ticker。
        start: 起始日期。
        end: 结束日期（含）。

    Returns:
        标准化后的 K 线 DataFrame；无数据或超时返回 None。
    """
    box: list[pd.DataFrame | None] = []

    def _run() -> None:
        try:
            res = provider.fetch_kline(ticker, start, end, frequency="d", adjustflag="1")
        except (RuntimeError, ValueError, OSError) as e:
            logger.debug(f"{provider.name} 拉取 {ticker} 失败: {e}")
            box.append(None)
            return
        if res is None or res.is_empty:
            box.append(None)
            return
        df = res.to_dataframe()
        box.append(df if len(df) else None)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(FETCH_TIMEOUT)
    if t.is_alive():
        logger.warning(f"{provider.name} 拉取 {ticker} 超时 {FETCH_TIMEOUT:.0f}s")
        return None
    return box[0] if box else None


def merge_increment(existing: pd.DataFrame, new: pd.DataFrame, end_date: str) -> pd.DataFrame:
    """把新拉取的 K 线合并进已有数据，并裁掉目标日期之后的部分。

    Args:
        existing: 缓存中已有的 K 线（按 timestamps 升序）。
        new: 本次拉取的 K 线。
        end_date: 目标结束日期（YYYY-MM-DD），超出的行会被丢弃。

    Returns:
        合并、去重、按日期升序排列后的 DataFrame。
    """
    new = new.copy()
    new["timestamps"] = pd.to_datetime(new["timestamps"])
    new = new[new["timestamps"] <= pd.Timestamp(end_date)]
    if existing is None or existing.empty:
        return new.sort_values("timestamps").reset_index(drop=True)

    old = existing.copy()
    old["timestamps"] = pd.to_datetime(old["timestamps"])
    if new.empty:
        return old.sort_values("timestamps").reset_index(drop=True)

    cutoff = old["timestamps"].max()
    append = new[new["timestamps"] > cutoff]
    if append.empty:
        return old.sort_values("timestamps").reset_index(drop=True)
    combined = pd.concat([old, append], ignore_index=True)
    combined = combined.drop_duplicates(subset=["timestamps"], keep="last")
    return combined.sort_values("timestamps").reset_index(drop=True)


def backfill_one(
    factory: DataProviderFactory,
    ticker: str,
    existing: pd.DataFrame | None,
    end_date: str,
) -> tuple[str, int, str]:
    """单只标的增量补齐到 end_date。

    Args:
        factory: DataProviderFactory。
        ticker: 带前缀 ticker。
        existing: 缓存中已有 K 线；None 表示无历史。
        end_date: 目标结束日期。

    Returns:
        (ticker, 新增行数, provider 名或状态)。状态为 "up_to_date" / "FAIL"。
    """
    if existing is not None and not existing.empty:
        last = pd.to_datetime(existing["timestamps"]).max()
        start = (last - pd.Timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    else:
        start = "2015-01-01"

    for provider_name in get_provider_chain(ticker):
        provider = factory.get_provider(provider_name)
        if provider is None:
            continue
        df = _fetch_one_provider(provider, ticker, start, end_date)
        if df is None:
            continue
        combined = merge_increment(existing if existing is not None else pd.DataFrame(), df, end_date)
        if combined.empty:
            continue
        before = 0 if existing is None or existing.empty else len(existing)
        added = len(combined) - before
        if added <= 0:
            return ticker, 0, "up_to_date"
        _set_kline_with_retry(
            ticker,
            combined["timestamps"].min().strftime("%Y-%m-%d"),
            combined["timestamps"].max().strftime("%Y-%m-%d"),
            combined,
        )
        return ticker, added, provider_name
    return ticker, 0, "FAIL"


def _set_kline_with_retry(ticker: str, start: str, end: str, df: pd.DataFrame) -> None:
    """写入 K 线，遇到 SQLite 锁竞争时指数退避重试。"""
    import sqlite3

    delay = 0.2
    last_err: Exception | None = None
    for attempt in range(8):
        try:
            get_cache().set_kline(
                ticker,
                start,
                end,
                "d",
                df,
                ttl=0.0,
                adjustflag="1",
            )
            return
        except sqlite3.OperationalError as e:
            last_err = e
            if "locked" not in str(e).lower():
                raise
            logger.debug(f"写入 {ticker} 遇到数据库锁，{delay:.1f}s 后重试 ({attempt+1}/8)")
            time.sleep(delay)
            delay = min(delay * 2, 2.0)
    raise sqlite3.OperationalError(f"写入 {ticker} 多次遇到数据库锁: {last_err}")


def _load_existing(ticker: str) -> pd.DataFrame | None:
    """读取缓存中该 ticker 的日线数据（无则返回 None）。"""
    conn = get_sqlite_conn(get_cache_db_path())
    try:
        row = conn.execute(
            "SELECT data FROM kline_cache WHERE ticker = ? AND freq = 'd' "
            "ORDER BY end DESC LIMIT 1",
            (ticker,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    try:
        return pd.read_pickle(BytesIO(row[0]))
    except (OSError, ValueError, pickle.UnpicklingError, AttributeError) as e:
        logger.warning(f"缓存解码失败 {ticker}: {e}")
        return None


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="增量补齐 K 线缓存到指定日期")
    parser.add_argument(
        "--end-date",
        default=_get_expected_date(),
        help="目标结束日期 YYYY-MM-DD（默认最近一个已收盘交易日）",
    )
    parser.add_argument(
        "--tickers",
        default=None,
        help="只补指定标的（逗号分隔，裸代码或带前缀均可）；缺省按缓存滞后清单",
    )
    parser.add_argument("--workers", type=int, default=4, help="并发数（默认 4，避免 SQLite 写锁竞争）")
    parser.add_argument("--limit", type=int, default=0, help="最多处理 N 只（0=全部）")
    parser.add_argument("--dry-run", action="store_true", help="只统计待补标的，不写库")
    return parser.parse_args()


def main() -> None:
    """主流程：查滞后 → 增量拉取 → 合并写库 → 汇总统计。"""
    args = parse_args()
    end_date = args.end_date
    db_path = get_cache_db_path()

    if args.tickers:
        tickers = [normalize_ticker_key(t.strip()) for t in args.tickers.split(",") if t.strip()]
        known = {r[0]: r for r in find_stale_tickers(db_path, "9999-12-31")}
        rows = [known.get(t, (t, "2015-01-01", "1970-01-01")) for t in tickers]
    else:
        rows = find_stale_tickers(db_path, end_date)

    if args.limit > 0:
        rows = rows[: args.limit]

    logger.info(f"🎯 目标结束日期: {end_date}")
    logger.info(f"📋 待补标的: {len(rows)} 只（结束日 < {end_date}）")
    if not rows:
        logger.info("✅ 全部标的已是最新，无需补齐")
        return

    if args.dry_run:
        preview = ", ".join(r[0] for r in rows[:20])
        logger.info(f"🔍 dry-run：不写库。样例：{preview}{' …' if len(rows) > 20 else ''}")
        return

    factory = get_data_factory()
    t0 = time.time()
    ok = no_new = failed = 0
    added_total = 0
    failed_tickers: list[str] = []
    lock = threading.Lock()

    def _worker(item: tuple[str, str, str]) -> tuple[str, int, str]:
        ticker = item[0]
        try:
            existing = _load_existing(ticker)
            return backfill_one(factory, ticker, existing, end_date)
        except (RuntimeError, ValueError, OSError, sqlite3.Error) as e:
            logger.error(f"补齐 {ticker} 失败: {type(e).__name__}: {e}")
            return ticker, 0, "FAIL"

    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_worker, r): r for r in rows}
        for fut in as_completed(futures):
            ticker, added, provider = fut.result()
            with lock:
                done += 1
                if provider == "FAIL":
                    failed += 1
                    failed_tickers.append(ticker)
                elif added == 0:
                    no_new += 1
                else:
                    ok += 1
                    added_total += added
                if done % 50 == 0 or done == len(rows):
                    logger.info(
                        f"  进度 {done}/{len(rows)} | 成功 {ok} | 无新数据 {no_new} | "
                        f"失败 {failed} | 新增行 {added_total}"
                    )

    elapsed = time.time() - t0
    logger.info("=" * 60)
    logger.info(f"✅ 补齐完成：成功 {ok} 只（+{added_total} 行）| 无新数据 {no_new} | 失败 {failed}")
    logger.info(f"   耗时 {elapsed:.1f}s ({elapsed / 60:.1f}min)")
    if failed_tickers:
        logger.warning(f"   失败样例（前 20）: {', '.join(failed_tickers[:20])}")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
