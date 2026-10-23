#!/usr/bin/env python3
"""重试补齐 3 只仍未到 2026-10-20 的股票。

- sh.601059 / sh.601198：A 股，tonghuashun API 返回空（停牌或无近期交易），
  尝试 baostock / mootdx 降级拉取 10-17 ~ 10-20 窗口
- hk.01448：港股，akshare_hk 返回空，尝试 tonghuashun / mootdx / akshare 降级

拉取窗口设为 2026-10-17 ~ 2026-10-20（比实际 end 10-16 晚一天，避免与已有数据重叠）。
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

# 窗口起点比现有 end (10-16) 晚一天，避免与已有记录重叠
TICKERS = [
    ("sh.601059", "2026-10-17"),
    ("sh.601198", "2026-10-17"),
    ("hk.01448", "2026-03-20"),
]
FETCH_END = "2026-10-20"


def try_provider_chain(
    ticker: str,
    fetch_start: str,
    factory,
) -> tuple[str, bool, str]:
    """按 provider_chain 依次尝试，返回 (ticker, success, detail)。"""
    cache = get_cache()
    provider_names = get_provider_chain(ticker)
    last_error = "no_provider_tried"

    for pname in provider_names:
        try:
            provider = factory.get_provider(pname)
            if provider is None:
                last_error = f"provider_missing:{pname}"
                continue

            result = provider.fetch_kline(ticker, fetch_start, FETCH_END, frequency="d", adjustflag="1")
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
            from io import BytesIO

            conn = sqlite3.connect(str(Path("outputs/cache/pipeline_cache.db")))
            row = conn.execute(
                "SELECT start, end, data FROM kline_cache WHERE ticker=?",
                (ticker,),
            ).fetchone()
            conn.close()

            if row:
                _, _, existing_data = row
                try:
                    existing_df = pd.read_pickle(BytesIO(existing_data))
                except Exception:
                    existing_df = pd.DataFrame()

                if not existing_df.empty:
                    ex_ts_col = "timestamps" if "timestamps" in existing_df.columns else "date"
                    existing_latest = pd.to_datetime(existing_df[ex_ts_col]).max()
                    new_rows = df[ts > existing_latest]
                    if len(new_rows) == 0:
                        return (ticker, True, "already_up_to_date")
                    combined = pd.concat([existing_df, new_rows], ignore_index=True)
                    n_new = len(new_rows)
                else:
                    combined = df
                    n_new = len(df)
            else:
                combined = df
                n_new = len(df)

            cache.set_kline(
                ticker,
                combined[ts_col].min().strftime("%Y-%m-%d"),
                combined[ts_col].max().strftime("%Y-%m-%d"),
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
    logger.info(f"重试补齐 {len(TICKERS)} 只滞后股票，目标更新至 {FETCH_END}")
    logger.info("=" * 60)

    ok_count = 0
    fail_count = 0
    failed: list[tuple[str, str]] = []

    for i, (ticker, fetch_start) in enumerate(TICKERS, 1):
        chain = get_provider_chain(ticker)
        logger.info(f"[{i}/{len(TICKERS)}] {ticker}  fetch_window={fetch_start}~{FETCH_END}  chain={chain}")
        t0 = time.time()
        _, success, detail = try_provider_chain(ticker, fetch_start, factory)
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
    logger.info("=" * 60)

    # 验证
    import sqlite3
    con = sqlite3.connect(str(Path("outputs/cache/pipeline_cache.db")))
    for ticker, _ in TICKERS:
        row = con.execute(
            "SELECT end FROM kline_cache WHERE ticker=?", (ticker,)
        ).fetchone()
        cur_end = row[0] if row else "N/A"
        status = "✅" if cur_end >= FETCH_END else "⚠️"
        logger.info(f"  {status} {ticker}  end={cur_end}")
    con.close()


if __name__ == "__main__":
    main()
