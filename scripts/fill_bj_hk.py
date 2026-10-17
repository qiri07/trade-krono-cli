#!/usr/bin/env python3
"""补齐北交所和港股的历史数据。"""

from __future__ import annotations

import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts._utils import get_provider_chain
from trade_krono_cli.cache import get_cache
from trade_krono_cli.cli_commands.core import _load_env
from trade_krono_cli.data_providers.factory import DataProviderFactory, get_data_factory

_START_DATE = "2020-01-01"
_END_DATE = "2026-10-13"
_WORKERS = 16


def _get_tickers(prefix: str) -> list[str]:
    """读取指定前缀（bj./hk.）的股票代码列表。"""
    db = Path("outputs/cache/pipeline_cache.db")
    conn = sqlite3.connect(str(db))
    tickers = [
        r[0]
        for r in conn.execute(
            "SELECT ticker FROM kline_cache WHERE ticker LIKE ? ORDER BY ticker",
            (f"{prefix}%",),
        ).fetchall()
    ]
    conn.close()
    return tickers


def _fetch_one(factory: DataProviderFactory, ticker: str, chain: list[str]) -> tuple[str, int, str]:
    try:
        for pname in chain:
            try:
                p = factory.get_provider(pname)
                if p is None:
                    continue
                result = p.fetch_kline(
                    ticker, _START_DATE, _END_DATE, frequency="d", adjustflag="1"
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
                return (ticker, len(df), pname)
            except Exception as e:
                logger.debug(f"provider {pname} 获取 {ticker} 失败: {e}")
                continue
        return (ticker, 0, "failed")
    except Exception as e:
        return (ticker, 0, f"error:{e}")


def _fill_group(
    factory: DataProviderFactory, label: str, tickers: list[str], progress_every: int
) -> int:
    """并发补齐一组股票（HK/BJ），返回成功条数。"""
    ok = 0
    with ThreadPoolExecutor(max_workers=_WORKERS) as exe:
        futures = {exe.submit(_fetch_one, factory, t, get_provider_chain(t)): t for t in tickers}
        for i, future in enumerate(as_completed(futures), 1):
            ticker = futures[future]
            try:
                _, rows, src = future.result()
                if rows > 0:
                    ok += 1
                    print(f"  ✅ {ticker}: {src} ({rows} 条)")
                else:
                    print(f"  ❌ {ticker}: {src}")
            except Exception:
                print(f"  ❌ {ticker}: 异常")
            if i % progress_every == 0:
                print(f"  {label} progress: {i}/{len(tickers)}")
    return ok


def main() -> None:
    _load_env()
    factory = get_data_factory()

    # ── HK stocks ──────────────────────────────────────────────
    hk_tickers = _get_tickers("hk.")
    print(f"HK stocks to fill: {len(hk_tickers)}")
    hk_ok = _fill_group(factory, "HK", hk_tickers, progress_every=3)
    print(f"HK fill done: {hk_ok}/{len(hk_tickers)} success\n")

    # ── BJ stocks ──────────────────────────────────────────────
    bj_tickers = _get_tickers("bj.")
    print(f"BJ stocks to fill: {len(bj_tickers)}")
    bj_ok = _fill_group(factory, "BJ", bj_tickers, progress_every=50)
    print(f"BJ fill done: {bj_ok}/{len(bj_tickers)} success")
    print(f"\nTotal: BJ {bj_ok}, HK {hk_ok}")


if __name__ == "__main__":
    main()
