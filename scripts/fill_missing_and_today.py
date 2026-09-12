#!/usr/bin/env python3
"""补齐失败股票历史数据 + 今日增量同步"""

from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Thread

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from trade_krono_cli.cache import get_cache
from trade_krono_cli.cli_commands.core import _load_env
from trade_krono_cli.data_providers.factory import get_data_factory

START_DATE = "2020-01-01"
END_DATE = "2026-09-12"
WORKERS = 8
FETCH_TIMEOUT = 45
CACHE_DB = Path("outputs/cache/pipeline_cache.db")

_load_env()


def get_failed_tickers() -> list[str]:
    """获取全量同步失败的13只非北交所股票。"""
    return [
        "sh.600027",
        "sh.600052",
        "sh.600053",
        "sh.600007",
        "sh.600794",
        "sh.600871",
        "sh.601858",
        "sh.603678",
        "sh.688005",
        "sh.688435",
        "sz.002845",
        "sz.002865",
        "sz.300364",
    ]


def get_today_only_tickers() -> list[str]:
    """获取只有今日数据的非北交所股票（需要补全历史）。"""
    import sqlite3

    conn = sqlite3.connect(str(CACHE_DB))
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT ticker FROM kline_cache
        WHERE start = '2026-09-12' AND end = '2026-09-12'
          AND ticker NOT LIKE 'bj.%'
        ORDER BY ticker
    """)
    tickers = [r[0] for r in cur.fetchall()]
    conn.close()
    return tickers


def get_provider_chain(ticker: str) -> list[str]:
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

    thread = Thread(target=_fetch, daemon=True)
    thread.start()
    thread.join(timeout=FETCH_TIMEOUT)

    if thread.is_alive():
        return ("timeout", 0, f"超时{FETCH_TIMEOUT}s")
    if result_container:
        return result_container[0]
    return ("failed", 0, "; ".join(error_container) if error_container else "无结果")


def fetch_today_incremental(
    factory, ticker: str, provider_chain: list[str]
) -> tuple[str, int, str]:
    """拉取今日增量数据。"""
    result_container: list[tuple[str, int, str]] = []
    error_container: list[str] = []

    def _fetch():
        for provider_name in provider_chain:
            try:
                provider = factory.get_provider(provider_name)
                if provider is None:
                    continue
                result = provider.fetch_kline(
                    ticker, "2026-09-12", "2026-09-12", frequency="d", adjustflag="1"
                )
                if result is None or result.is_empty:
                    continue
                df = result.to_dataframe()
                if len(df) == 0:
                    continue
                cache = get_cache()
                ts = pd.to_datetime(df["timestamps"])
                # 检查是否已存在今日数据，如存在则更新
                cur_result = cache.get_kline(ticker, "2026-09-12", "2026-09-12", "d", "1")
                if cur_result is not None and len(cur_result) > 0:
                    # 合并：保留历史数据，追加/更新今日数据
                    combined = pd.concat([cur_result, df], ignore_index=True)
                    combined = combined.drop_duplicates(subset=["timestamps"], keep="last")
                    combined = combined.sort_values("timestamps").reset_index(drop=True)
                    cache.set_kline(
                        ticker,
                        combined["timestamps"].min().strftime("%Y-%m-%d"),
                        combined["timestamps"].max().strftime("%Y-%m-%d"),
                        "d",
                        combined,
                        ttl=0.0,
                    )
                else:
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

    thread = Thread(target=_fetch, daemon=True)
    thread.start()
    thread.join(timeout=FETCH_TIMEOUT)

    if thread.is_alive():
        return ("timeout", 0, f"超时{FETCH_TIMEOUT}s")
    if result_container:
        return result_container[0]
    return ("failed", 0, "; ".join(error_container) if error_container else "无结果")


def main() -> None:
    factory = get_data_factory()

    # ── 第一步：补齐13只失败股票的历史数据 ─────────────────────
    print("=" * 60)
    print("📌 第一步：补齐13只失败股票的历史数据")
    print("=" * 60)
    failed_tickers = get_failed_tickers()
    success_13 = 0
    failed_13 = []
    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        futures = {
            executor.submit(fetch_full_range, factory, t, get_provider_chain(t)): t
            for t in failed_tickers
        }
        for i, future in enumerate(as_completed(futures), 1):
            ticker = futures[future]
            try:
                provider, n_rows, detail = future.result(timeout=FETCH_TIMEOUT + 5)
                if provider == "timeout":
                    failed_13.append((ticker, f"超时 {FETCH_TIMEOUT}s"))
                    print(f"  ⏱️  {i}/{len(failed_tickers)} 超时: {ticker}")
                elif provider == "failed":
                    failed_13.append((ticker, detail))
                    print(f"  ❌ {i}/{len(failed_tickers)} 失败: {ticker} ({detail[:60]})")
                else:
                    success_13 += 1
                    print(
                        f"  ✅ {i}/{len(failed_tickers)} {ticker}: {provider} 拉取 {n_rows} 条 {detail}"
                    )
            except Exception as e:
                failed_13.append((ticker, str(e)))
                print(f"  ❌ {i}/{len(failed_tickers)} 异常: {ticker} ({e})")
    print(
        f"\n第一步结果: 成功 {success_13}/{len(failed_tickers)}, 失败 {len(failed_13)}/{len(failed_tickers)}"
    )
    if failed_13:
        print(f"   仍失败: {[t for t, _ in failed_13]}")
    print()

    # ── 第二步：检查哪些股票缺少2026-09-12数据 ──────────────────
    print("=" * 60)
    print("📌 第二步：同步今日（2026-09-12）增量数据")
    print("=" * 60)
    import sqlite3

    conn = sqlite3.connect(str(CACHE_DB))
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT ticker FROM kline_cache
        WHERE ticker NOT IN (
            SELECT DISTINCT ticker FROM kline_cache
            WHERE end >= '2026-09-12'
        )
        AND ticker NOT LIKE 'bj.%'
        ORDER BY ticker
    """)
    need_today = [r[0] for r in cur.fetchall()]
    conn.close()
    print(f"需要补今日数据的股票: {len(need_today)} 只（非北交所）")
    print()

    # 分批同步今日增量
    success_today = 0
    failed_today = []
    batch_size = 50
    for batch_start in range(0, len(need_today), batch_size):
        batch = need_today[batch_start : batch_start + batch_size]
        print(f"  批次 [{batch_start + 1}~{batch_start + len(batch)}/{len(need_today)}]")
        with ThreadPoolExecutor(max_workers=WORKERS) as executor:
            futures = {
                executor.submit(fetch_today_incremental, factory, t, get_provider_chain(t)): t
                for t in batch
            }
            for future in as_completed(futures):
                ticker = futures[future]
                try:
                    provider, n_rows, detail = future.result(timeout=FETCH_TIMEOUT + 5)
                    if provider in ("timeout", "failed"):
                        failed_today.append((ticker, detail))
                    else:
                        success_today += 1
                except Exception as e:
                    failed_today.append((ticker, str(e)))
        time.sleep(1)  # 短暂休息避免API限流

    print(
        f"\n第二步结果: 成功 {success_today}/{len(need_today)}, 失败 {len(failed_today)}/{len(need_today)}"
    )
    if failed_today:
        print(f"   仍失败: {[t for t, _ in failed_today]}")
    print()

    # ── 第三步：最终统计 ───────────────────────────────────────
    print("=" * 60)
    print("📌 第三步：最终数据统计")
    print("=" * 60)
    conn = sqlite3.connect(str(CACHE_DB))
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM kline_cache")
    total = cur.fetchone()[0]
    cur.execute("SELECT COUNT(DISTINCT ticker) FROM kline_cache")
    tickers = cur.fetchone()[0]
    cur.execute("SELECT MAX(end) FROM kline_cache")
    latest = cur.fetchone()[0]
    cur.execute("""
        SELECT COUNT(DISTINCT ticker) FROM kline_cache
        WHERE end >= '2026-09-12'
    """)
    has_today = cur.fetchone()[0]
    cur.execute("""
        SELECT COUNT(DISTINCT ticker) FROM kline_cache
        WHERE start <= '2020-12-31' AND end >= '2020-01-01'
    """)
    has_2020 = cur.fetchone()[0]
    conn.close()

    print(f"  总记录数:   {total}")
    print(f"  股票数:     {tickers}")
    print(f"  最新日期:   {latest}")
    print(f"  今日数据:   {has_today}/{tickers} ({has_today * 100 / tickers:.1f}%)")
    print(f"  2020年数据: {has_2020}/{tickers} ({has_2020 * 100 / tickers:.1f}%)")
    print("=" * 60)
    print("✅ 全部完成")


if __name__ == "__main__":
    main()
