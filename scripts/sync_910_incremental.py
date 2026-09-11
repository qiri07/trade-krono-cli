#!/usr/bin/env python3
"""同步缺少 9.10 日数据的非北交所股票增量。

策略：
  - 仅拉取 2026-09-10 单日（非全量，速度快）
  - baostock 主（免费无严格限流），tonghuashun 兜底（北交所除外）
  - 2 并发避免 baostock 连接池压力
  - 每批 50 只 + 批次间休息 5s
"""

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

END_DATE = "2026-09-10"
WORKERS = 2
BATCH_SIZE = 50
FETCH_TIMEOUT = 20
BACKOFF = 5  # 批次间休息秒数
CACHE_DB = Path("outputs/cache/pipeline_cache.db")

_load_env()


def get_missing_today_tickers() -> list[str]:
    """获取缺少 9.10 数据的非北交所股票。"""
    import sqlite3

    conn = sqlite3.connect(str(CACHE_DB))
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT ticker FROM kline_cache
        WHERE ticker NOT LIKE 'bj.%'
        AND ticker NOT IN (
            SELECT DISTINCT ticker FROM kline_cache WHERE end >= '2026-09-10'
        )
        ORDER BY ticker
    """)
    tickers = [r[0] for r in cur.fetchall()]
    conn.close()
    return tickers


def get_provider_chain(ticker: str) -> list[str]:
    """根据股票类型返回 provider 优先级链（baostock 优先用于大批量）。"""
    if ticker.startswith("bj."):
        return ["tonghuashun"]
    elif ticker.startswith("sh.") or ticker.startswith("sz."):
        return ["baostock", "tonghuashun"]
    return ["baostock", "tonghuashun"]


def fetch_today_only(factory, ticker: str, provider_chain: list[str]) -> tuple[str, int, str]:
    """拉取单只股票 2026-09-10 增量，合并到已有历史数据中。"""
    result_container: list[tuple[str, int, str]] = []
    error_container: list[str] = []

    def _fetch():
        for provider_name in provider_chain:
            try:
                provider = factory.get_provider(provider_name)
                if provider is None:
                    continue
                result = provider.fetch_kline(
                    ticker, END_DATE, END_DATE, frequency="d", adjustflag="1"
                )
                if result is None or result.is_empty:
                    continue
                df = result.to_dataframe()
                if len(df) == 0:
                    continue
                cache = get_cache()
                ts = pd.to_datetime(df["timestamps"])
                # 获取已有历史数据并合并
                cur_result = cache.get_kline(ticker, "2020-01-01", END_DATE, "d", "1")
                if cur_result is not None and len(cur_result) > 0:
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
    tickers = get_missing_today_tickers()
    total = len(tickers)

    print("=" * 60)
    print(f"📌 同步 9.10 增量数据（共 {total} 只非北交所股票，baostock 主）")
    print("=" * 60)

    success = 0
    failed: list[tuple[str, str]] = []
    batch_num = 0

    for batch_start in range(0, total, BATCH_SIZE):
        batch = tickers[batch_start : batch_start + BATCH_SIZE]
        batch_num += 1
        print(f"\n  批次 {batch_num}: [{batch_start + 1}~{batch_start + len(batch)}/{total}]")

        with ThreadPoolExecutor(max_workers=WORKERS) as executor:
            futures = {
                executor.submit(fetch_today_only, factory, t, get_provider_chain(t)): t
                for t in batch
            }
            for future in as_completed(futures):
                ticker = futures[future]
                try:
                    provider, n_rows, detail = future.result(timeout=FETCH_TIMEOUT + 5)
                    if provider in ("timeout", "failed"):
                        failed.append((ticker, detail))
                    else:
                        success += 1
                except Exception as e:
                    failed.append((ticker, str(e)))
        time.sleep(BACKOFF)

    print(f"\n{'=' * 60}")
    print(f"✅ 成功: {success}/{total}")
    print(f"❌ 失败: {len(failed)}/{total}")
    if failed:
        print("   失败列表（前20）:")
        for t, d in failed[:20]:
            print(f"     {t}: {d[:60]}")
    print("=" * 60)


if __name__ == "__main__":
    main()
