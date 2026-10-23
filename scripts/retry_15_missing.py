#!/usr/bin/env python3
"""重试补齐 15 只（13 港股 + 2 A股）最新 K 线数据到 2026-10-20。

- 港股（hk.*）：主用 akshare_hk，失败降级 tonghuashun
- A 股（sh./sz.）：主用 baostock，失败降级 tonghuashun

按 provider_chain 依次降级，拉取增量窗口 2026-10-16 ~ 2026-10-20，合并写入 cache。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
from loguru import logger

from scripts._utils import get_provider_chain
from trade_krono_cli.cache import get_cache
from trade_krono_cli.cli_commands.core import _load_env
from trade_krono_cli.data_providers.factory import get_data_factory

FETCH_START = "2026-10-16"
FETCH_END = "2026-10-20"

TICKERS = [
    # 港股（13）
    "hk.00386", "hk.00390", "hk.00700", "hk.00941", "hk.00992",
    "hk.01066", "hk.01448", "hk.01658", "hk.01810", "hk.01919",
    "hk.02610", "hk.09988", "hk.09992",
    # A 股（2）
    "sh.601059", "sh.601198",
]


def fetch_and_append(ticker: str, factory) -> tuple[str, bool, str]:
    """按 provider_chain 降级拉取增量并合并写入 cache，返回 (ticker, success, detail)。"""
    cache = get_cache()
    provider_names = get_provider_chain(ticker)
    last_error = "no_provider"

    for pname in provider_names:
        try:
            provider = factory.get_provider(pname)
            if provider is None:
                last_error = f"provider_missing:{pname}"
                continue

            result = provider.fetch_kline(ticker, FETCH_START, FETCH_END, frequency="d", adjustflag="1")
            if result is None or result.is_empty:
                last_error = f"empty:{pname}"
                continue

            df = result.to_dataframe()
            if df is None or len(df) == 0:
                last_error = f"empty_df:{pname}"
                continue

            ts_col = "timestamps" if "timestamps" in df.columns else "date"
            ts = pd.to_datetime(df[ts_col])

            # 读现有数据
            import sqlite3
            conn = sqlite3.connect(str(Path("outputs/cache/pipeline_cache.db")))
            row = conn.execute(
                "SELECT start, end, data FROM kline_cache WHERE ticker=?",
                (ticker,),
            ).fetchone()
            conn.close()

            if row:
                _, _, existing_data = row
                try:
                    from io import BytesIO
                    existing_df = pd.read_pickle(BytesIO(existing_data))
                except Exception:
                    existing_df = pd.DataFrame()

                if existing_df.empty:
                    combined = df
                    n_new = len(df)
                else:
                    existing_ts_col = (
                        "timestamps" if "timestamps" in existing_df.columns else "date"
                    )
                    existing_latest = pd.to_datetime(existing_df[existing_ts_col]).max()
                    new_rows = df[ts > existing_latest]
                    if len(new_rows) == 0:
                        return (ticker, True, "already_up_to_date")
                    combined = pd.concat([existing_df, new_rows], ignore_index=True)
                    n_new = len(new_rows)
            else:
                combined = df
                n_new = len(df)

            cache.set_kline(
                ticker,
                combined[ts_col].min().strftime("%Y-%m-%d") if ts_col in combined.columns else FETCH_START,
                combined[ts_col].max().strftime("%Y-%m-%d") if ts_col in combined.columns else FETCH_END,
                freq="d",
                df=combined,
                ttl=0.0,
                adjustflag="1",
            )
            return (ticker, True, f"{n_new}rows via {pname}")

        except Exception as e:
            last_error = f"{pname}:{type(e).__name__}:{str(e)[:60]}"
            continue

    return (ticker, False, last_error)


def main() -> None:
    _load_env()
    factory = get_data_factory()
    logger.info("=" * 60)
    logger.info(f"重试补齐 {len(TICKERS)} 只（13 港股 + 2 A股），窗口 {FETCH_START}~{FETCH_END}")
    logger.info("=" * 60)

    ok_count = 0
    fail_count = 0
    failed: list[tuple[str, str]] = []

    for i, ticker in enumerate(TICKERS, 1):
        chain = get_provider_chain(ticker)
        logger.info(f"[{i}/{len(TICKERS)}] {ticker}  chain={chain}")
        t0 = time.time()
        _, success, detail = fetch_and_append(ticker, factory)
        elapsed = time.time() - t0
        if success:
            ok_count += 1
            logger.info(f"  ✅ {detail}  ({elapsed:.1f}s)")
        else:
            fail_count += 1
            failed.append((ticker, detail))
            logger.warning(f"  ❌ {detail}  ({elapsed:.1f}s)")

    logger.info("\n" + "=" * 60)
    logger.info(f"完成：成功 {ok_count}，失败 {fail_count}，总计 {len(TICKERS)}")
    if failed:
        logger.warning(f"仍失败：{', '.join(f'{t} ({d})' for t, d in failed)}")
    else:
        logger.info("全部成功 ✅")
    logger.info("=" * 60)

    # 验证
    import sqlite3
    con = sqlite3.connect(str(Path("outputs/cache/pipeline_cache.db")))
    placeholders = ",".join("?" for _ in TICKERS)
    lagging = con.execute(
        f"SELECT ticker, end FROM kline_cache WHERE ticker IN ({placeholders}) AND end < ? ORDER BY ticker",
        [*TICKERS, "2026-10-20"],
    ).fetchall()
    con.close()
    if lagging:
        logger.warning(f"仍未到 2026-10-20（{len(lagging)} 只）：{lagging}")
    else:
        logger.info("验证通过：所有 15 只均已更新至 2026-10-20 ✅")


if __name__ == "__main__":
    main()
