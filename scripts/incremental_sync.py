#!/usr/bin/env python3
"""增量同步：补齐缺失 Sep 10+/Sep 11 的 353 只股票。

数据源分析：
  - tonghuashun: 唯一有 Sep 11 数据的 provider（沪深 + 北交所）
  - baostock: 最新到 Sep 10，且有空字符串 crash bug（已修复）
  - mootdx: 沪深/北交所均返回空数据
策略：所有缺失 ticker 统一走 tonghuashun（最可靠）
"""

from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
from loguru import logger

from trade_krono_cli.cache import get_cache
from trade_krono_cli.cli_commands.core import _load_env
from trade_krono_cli.data_providers.factory import get_data_factory

WORKERS = 20
BATCH_SIZE = 80
FETCH_TIMEOUT = 30
CACHE_DB = Path("outputs/cache/pipeline_cache.db")
END_DATE = "2026-09-14"
START_DATE = "2026-09-01"

_load_env()


def get_missing_tickers() -> list[str]:
    """获取缺少今日数据的 ticker。"""
    import sqlite3

    conn = sqlite3.connect(str(CACHE_DB))
    rows = conn.execute(
        """
        SELECT ticker, MAX(end) as latest FROM kline_cache
        GROUP BY ticker HAVING MAX(end) < ?
        ORDER BY ticker
    """,
        (END_DATE,),
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def fetch_one(factory, ticker: str, provider_name: str) -> tuple[str, bool]:
    """拉取单只股票的新数据并追加到现有记录（不覆盖历史）。"""
    try:
        provider = factory.get_provider(provider_name)
        if provider is None:
            return (ticker, False)
        result = provider.fetch_kline(ticker, START_DATE, END_DATE, frequency="d", adjustflag="1")
        if result is None or result.is_empty:
            return (ticker, False)
        df = result.to_dataframe()
        if df is None or len(df) == 0:
            return (ticker, False)

        ts_col = "timestamps" if "timestamps" in df.columns else "date"
        ts = pd.to_datetime(df[ts_col])

        # 从数据库读取现有完整记录
        import sqlite3

        conn = sqlite3.connect(str(CACHE_DB))
        row = conn.execute(
            "SELECT start, end, data FROM kline_cache WHERE ticker = ?", (ticker,)
        ).fetchone()
        conn.close()

        if row:
            existing_start, existing_end, existing_data = row
            try:
                from io import BytesIO

                existing_df = pd.read_pickle(BytesIO(existing_data))
            except Exception:
                existing_df = pd.DataFrame()
            # 过滤出真正的增量数据（晚于现有最后一天）
            new_data = df[ts > pd.Timestamp(existing_end)]
            if len(new_data) == 0:
                return (ticker, True)  # 已有更新数据，无需写入
            # 追加：合并新旧数据
            combined = pd.concat([existing_df, new_data], ignore_index=True)
        else:
            # 新股票，无历史记录
            combined = df

        # 写入合并后的完整数据（避免 set_kline 的 DELETE 逻辑覆盖历史）
        cache = get_cache()
        cache.set_kline(
            ticker,
            combined[ts_col].iloc[0].strftime("%Y-%m-%d"),
            combined[ts_col].iloc[-1].strftime("%Y-%m-%d"),
            freq="d",
            df=combined,
            ttl=0.0,
            adjustflag="1",
        )
        return (ticker, True)
    except Exception as e:
        logger.warning(f"fetch failed [{ticker}]: {e}")
        return (ticker, False)


def main() -> None:
    logger.info("=" * 60)
    logger.info("增量同步：补齐 Sep 10+/Sep 11 缺失数据（tonghuashun 为主）")
    logger.info("=" * 60)

    missing = get_missing_tickers()
    bj_tickers = [t for t in missing if t.startswith("bj.")]
    shsz_tickers = [t for t in missing if not t.startswith("bj.")]
    logger.info(f"共 {len(missing)} 只需要补齐: bj={len(bj_tickers)}, sh/sz={len(shsz_tickers)}")

    if not missing:
        logger.info("无需同步")
        return

    factory = get_data_factory()
    success = 0
    failed = 0

    # ── tonghuashun 批次 ────────────────────────────────────────
    all_tickers = bj_tickers + shsz_tickers
    batches = [all_tickers[i : i + BATCH_SIZE] for i in range(0, len(all_tickers), BATCH_SIZE)]
    logger.info(f"分 {len(batches)} 批，每批 {BATCH_SIZE} 只，使用 tonghuashun")

    for batch_idx, batch in enumerate(batches):
        t0 = time.time()
        batch_ok = 0
        batch_fail = 0
        with ThreadPoolExecutor(max_workers=WORKERS) as executor:
            futures = {executor.submit(fetch_one, factory, t, "tonghuashun"): t for t in batch}
            for future in as_completed(futures):
                try:
                    _, ok = future.result(timeout=FETCH_TIMEOUT)
                    if ok:
                        batch_ok += 1
                        success += 1
                    else:
                        batch_fail += 1
                        failed += 1
                except Exception:
                    batch_fail += 1
                    failed += 1
        elapsed = time.time() - t0
        logger.info(
            f"  批次 {batch_idx + 1}/{len(batches)}: "
            f"成功 {batch_ok}/{len(batch)}, 失败 {batch_fail}, "
            f"耗时 {elapsed:.1f}s"
        )
        time.sleep(0.2)

    logger.info("=" * 60)
    logger.info(f"完成！成功 {success}，失败 {failed}，总计 {len(missing)}")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
