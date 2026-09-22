#!/usr/bin/env python3
"""重拉取全量同步失败的历史数据 — 动态检测缺失股票并重试。"""

from __future__ import annotations

import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from trade_krono_cli.cache import get_cache
from trade_krono_cli.cli_commands.core import _load_env
from trade_krono_cli.data_providers.factory import get_data_factory

START_DATE = "2020-01-01"
# 以今日为上限，动态适配
END_DATE = date.today().isoformat()
WORKERS = 16
FETCH_TIMEOUT = 30
BATCH_DELAY = 2.0
CACHE_DB = Path("outputs/cache/pipeline_cache.db")

_load_env()


def get_incomplete_tickers() -> list[str]:
    """获取缺少今日数据的非北交所股票。"""
    import sqlite3

    conn = sqlite3.connect(str(CACHE_DB))
    cur = conn.cursor()
    # 真正缺今日数据的股票：不存在任何 start >= 今日 的记录
    cur.execute(
        """
        SELECT DISTINCT k.ticker FROM kline_cache k
        WHERE k.ticker NOT LIKE 'bj.%'
          AND NOT EXISTS (
              SELECT 1 FROM kline_cache k2
              WHERE k2.ticker = k.ticker AND k2.start >= ?
          )
        ORDER BY k.ticker
        """,
        (END_DATE,),
    )
    tickers = [r[0] for r in cur.fetchall()]
    conn.close()
    return tickers


def get_provider_chain(ticker: str) -> list[str]:
    """根据股票类型返回 provider 优先级链。"""
    if ticker.startswith("bj."):
        return ["tonghuashun"]
    elif ticker.startswith("sh.") or ticker.startswith("sz."):
        return ["tonghuashun", "baostock"]
    return ["baostock", "tonghuashun"]


def fetch_full_range(
    factory, ticker: str, provider_chain: list[str]
) -> tuple[str, int, str]:
    """带超时的全量历史数据拉取。"""
    result_container: list[tuple[str, int, str]] = []
    error_container: list[str] = []

    def _fetch() -> None:
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
                result_container.append(
                    (
                        provider_name,
                        len(df),
                        f"{ts.min().strftime('%Y-%m-%d')}~{ts.max().strftime('%Y-%m-%d')}",
                    )
                )
                return
            except Exception as e:
                error_container.append(f"{provider_name}:{e}")
                continue

    thread = threading.Thread(target=_fetch, daemon=True)
    thread.start()
    thread.join(timeout=FETCH_TIMEOUT)

    if thread.is_alive():
        return ("timeout", 0, f"超时{FETCH_TIMEOUT}s")
    if result_container:
        return result_container[0]
    return ("failed", 0, "; ".join(error_container) if error_container else "无结果")


def main() -> None:
    tickers = get_incomplete_tickers()
    if not tickers:
        print("✅ 没有需要重拉的股票")
        return

    print(f"🚀 发现 {len(tickers)} 只缺少历史数据的股票，开始重试...")
    print(f"   范围: {START_DATE} ~ {END_DATE}")
    print()

    factory = get_data_factory()
    success = 0
    failed = 0
    failed_list: list[tuple[str, str]] = []
    total = len(tickers)

    batch_size = 64
    for batch_start in range(0, total, batch_size):
        batch = tickers[batch_start : batch_start + batch_size]
        batch_num = batch_start // batch_size + 1
        print(f"  批次 {batch_num} ({batch_start + 1}~{min(batch_start + batch_size, total)}/{total})")

        with ThreadPoolExecutor(max_workers=WORKERS) as executor:
            futures = {
                executor.submit(fetch_full_range, factory, t, get_provider_chain(t)): t
                for t in batch
            }
            for future in as_completed(futures):
                ticker = futures[future]
                try:
                    provider, n_rows, detail = future.result(timeout=FETCH_TIMEOUT + 5)
                    if provider == "timeout":
                        failed += 1
                        failed_list.append((ticker, f"超时 {FETCH_TIMEOUT}s"))
                        print(f"    ⏱️  {ticker}")
                    elif provider == "failed":
                        failed += 1
                        failed_list.append((ticker, detail))
                        print(f"    ❌ {ticker} ({detail[:40]})")
                    else:
                        success += 1
                        print(
                            f"    ✅ {ticker}: {provider} {n_rows}条 {detail}"
                        )
                except Exception as e:
                    failed += 1
                    failed_list.append((ticker, str(e)))
                    print(f"    ❌ {ticker} ({e})")

        # 批次间短暂休息
        if batch_start + batch_size < total:
            time.sleep(BATCH_DELAY)

    print()
    print("=" * 50)
    print(f"✅ 成功: {success}/{total}")
    print(f"❌ 失败: {failed}/{total}")
    if failed_list:
        print("\n失败股票（前20只）:")
        for t, reason in failed_list[:20]:
            print(f"  {t}: {reason}")
    print("=" * 50)


if __name__ == "__main__":
    main()
