#!/usr/bin/env python3
"""重拉取全量同步失败的历史数据（仅针对只有今日数据的股票）"""
from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Thread

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from trade_krono_cli.cache import get_cache
from trade_krono_cli.cli_commands.core import _load_env
from trade_krono_cli.data_providers.factory import get_data_factory

START_DATE = "2020-01-01"
END_DATE = "2026-09-10"
WORKERS = 16
FETCH_TIMEOUT = 30  # 更长超时
CACHE_DB = Path("outputs/cache/pipeline_cache.db")

_load_env()


def get_incomplete_tickers() -> list[str]:
    """获取只有今日数据、缺少历史数据的股票。"""
    import sqlite3
    conn = sqlite3.connect(str(CACHE_DB))
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT ticker FROM kline_cache
        WHERE start = '2026-09-10' AND end = '2026-09-10'
          AND ticker NOT LIKE 'bj.%'
        ORDER BY ticker
    """)
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


def fetch_full_range(factory, ticker: str, provider_chain: list[str]) -> tuple[str, int, str]:
    """带超时的全量历史数据拉取。"""
    result_container: list[tuple[str, int, str]] = []
    error_container: list[str] = []

    def _fetch():
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
                    ttl=86400 * 365 * 10,
                )
                result_container.append((provider_name, len(df), f"{ts.min().strftime('%Y-%m-%d')}~{ts.max().strftime('%Y-%m-%d')}"))
                return
            except Exception as e:
                error_container.append(f"{provider_name}:{e}")
                continue

    thread = Thread(target=_fetch, daemon=True)
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

    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        futures = {executor.submit(fetch_full_range, factory, t, get_provider_chain(t)): t for t in tickers}
        for i, future in enumerate(as_completed(futures), 1):
            ticker = futures[future]
            try:
                provider, n_rows, detail = future.result(timeout=FETCH_TIMEOUT + 5)
                if provider == "timeout":
                    failed += 1
                    failed_list.append((ticker, f"超时 {FETCH_TIMEOUT}s"))
                    print(f"  ⏱️  {i}/{len(tickers)} 超时: {ticker}")
                elif provider == "failed":
                    failed += 1
                    failed_list.append((ticker, detail))
                    print(f"  ❌ {i}/{len(tickers)} 失败: {ticker} ({detail[:50]})")
                else:
                    success += 1
                    print(f"  ✅ {i}/{len(tickers)} {ticker}: {provider} 拉取 {n_rows} 条 {detail}")
            except Exception as e:
                failed += 1
                failed_list.append((ticker, str(e)))
                print(f"  ❌ {i}/{len(tickers)} 异常: {ticker} ({e})")

    print()
    print("=" * 50)
    print(f"✅ 成功: {success}/{len(tickers)}")
    print(f"❌ 失败: {failed}/{len(tickers)}")
    if failed_list:
        print("\n失败股票（前20只）:")
        for t, reason in failed_list[:20]:
            print(f"  {t}: {reason}")
    print("=" * 50)


if __name__ == "__main__":
    main()
