#!/usr/bin/env python3
"""港股 K 线同步脚本（默认增量，``--full`` 全量重建）。

行为说明
--------
- **默认（增量）**：读取每只票缓存中的最后交易日，只补拉「最后交易日之后」的
  K 线，并与既有历史合并后写回。因此对已缓存的票会真正更新到最新交易日，
  而不是「库里有这只票就跳过」。
- ``--full``：忽略缓存，从 ``--start`` 重新拉完整历史并覆盖该票记录。
- ``--force``：强制重新拉取选中的票（窗口仍按增量起点）。

数据源固定为 ``akshare_hk``（港股专属 Provider），不再走默认降级链，
避免 baostock 等不支持 ``hk.*`` 的源报「股票代码应为9位」。

用法：
    uv run python scripts/full_hk_history_sync.py                    # 增量补到最近交易日
    uv run python scripts/full_hk_history_sync.py --full             # 全量重建（2020 起）
    uv run python scripts/full_hk_history_sync.py --codes "00700,00941"
    uv run python scripts/full_hk_history_sync.py --end 2026-11-23 --dry-run
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from pathlib import Path
from typing import Any  # factory 为 DataProviderFactory（避免仅类型导入形成环）

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger

from scripts._utils import expected_trading_day, get_cache_db_path, get_sqlite_conn
from trade_krono_cli.cache.kline import _compute_data_hash
from trade_krono_cli.cli_commands.core import _load_env
from trade_krono_cli.data_providers.factory import get_data_factory

CACHE_DB = get_cache_db_path()
START_DATE = "2020-01-01"
HK_PROVIDER = "akshare_hk"
WORKERS = 3  # 港股数据源较敏感，保守并发


def get_hk_ticker_list() -> list[str]:
    """通过 akshare 获取全量港股代码列表。

    Returns:
        港股 ticker 列表；失败返回空列表。
    """
    import akshare as ak

    logger.info("📊 正在获取全量港股列表...")
    try:
        df = ak.stock_hk_spot_em()
    except Exception as e:  # akshare 网络层异常类型不稳定
        logger.warning(f"⚠️  东财港股列表获取失败: {e}")
        df = None
    if df is not None and not df.empty:
        codes = df["代码"].astype(str).str.zfill(5).tolist()
        tickers = [f"hk.{code}" for code in codes]
        logger.info(f"✅ 获取到 {len(tickers)} 只港股")
        return tickers

    # 东财不可用时使用本地缓存中的港股列表（不影响增量同步存量标的）
    cached = sorted(get_cached_hk_ends())
    if cached:
        logger.warning(f"⚠️  已回退到本地缓存港股列表（{len(cached)} 只）")
    return cached


def get_cached_hk_ends() -> dict[str, str]:
    """读取缓存中每只港股的最后交易日。

    Returns:
        ``{ticker: "YYYY-MM-DD"}``。
    """
    if not CACHE_DB.exists():
        return {}
    conn = get_sqlite_conn(CACHE_DB, readonly=True)
    try:
        rows = conn.execute(
            "SELECT ticker, MAX(end) FROM kline_cache "
            "WHERE ticker LIKE 'hk.%' AND freq='d' GROUP BY ticker"
        ).fetchall()
    finally:
        conn.close()
    return {r[0]: r[1] for r in rows if r[1]}


def _load_cached_df(ticker: str) -> pd.DataFrame | None:
    """读取单只票的现有 K 线（取 end 最新的一条记录）。"""
    conn = get_sqlite_conn(CACHE_DB, readonly=True)
    try:
        row = conn.execute(
            "SELECT data FROM kline_cache WHERE ticker=? AND freq='d' AND adjustflag='1' "
            "ORDER BY end DESC LIMIT 1",
            (ticker,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    try:
        return pd.read_pickle(BytesIO(row[0]))
    except (OSError, ValueError, pd.errors.ParserError) as e:
        logger.warning(f"⚠️  读取 {ticker} 现有缓存失败: {e}")
        return None


def _write_df(ticker: str, df: pd.DataFrame) -> None:
    """把 K 线写回缓存（覆盖该 ticker 的日线后复权记录）。"""
    buf = BytesIO()
    df.to_pickle(buf)
    buf.seek(0)
    raw_bytes = buf.read()
    data_hash = _compute_data_hash(raw_bytes)
    start_str = df["timestamps"].iloc[0].strftime("%Y-%m-%d")
    end_str = df["timestamps"].iloc[-1].strftime("%Y-%m-%d")

    conn = get_sqlite_conn(CACHE_DB)
    try:
        conn.execute(
            "DELETE FROM kline_cache WHERE ticker=? AND freq=? AND adjustflag=?",
            (ticker, "d", "1"),
        )
        conn.execute(
            "INSERT INTO kline_cache "
            "(ticker, start, end, freq, adjustflag, ttl, data, data_hash, created) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ticker, start_str, end_str, "d", "1", 0.0, raw_bytes, data_hash, time.time()),
        )
        conn.commit()
    finally:
        conn.close()


def _normalize_df(df: pd.DataFrame, ticker: str) -> pd.DataFrame | None:
    """统一为 ``timestamps`` 列并按时间升序去重。"""
    if df is None or len(df) == 0:
        return None
    if "timestamps" not in df.columns:
        if "date" not in df.columns:
            logger.debug(f"{ticker}: 缺少时间列")
            return None
        df = df.rename(columns={"date": "timestamps"})
    df = df.copy()
    df["timestamps"] = pd.to_datetime(df["timestamps"])
    df = df.sort_values("timestamps").drop_duplicates(subset=["timestamps"], keep="last")
    return df.reset_index(drop=True)


def fetch_and_store_hk(
    factory: Any,
    ticker: str,
    start_date: str,
    end_date: str,
    full: bool = False,
    cached_end: str | None = None,
) -> tuple[str, bool, str]:
    """拉取单只港股 K 线并与既有缓存合并写入。

    Args:
        factory: ``DataProviderFactory`` 实例。
        ticker: ``hk.00700`` 形式。
        start_date: 全量模式起始日期。
        end_date: 目标结束日期。
        full: 是否全量重建（忽略既有历史）。
        cached_end: 该票已缓存的最后交易日（``None`` 表示无缓存）。

    Returns:
        ``(ticker, success, detail)``。
    """
    try:
        if full or cached_end is None:
            fetch_start = start_date
        else:
            fetch_start = (pd.Timestamp(cached_end) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")

        if pd.Timestamp(fetch_start) > pd.Timestamp(end_date):
            return (ticker, True, "already_up_to_date")

        result = factory.fetch_kline_from_provider(
            HK_PROVIDER, ticker, fetch_start, end_date, frequency="d", adjustflag="1"
        )
        if result is None or result.is_empty:
            return (ticker, False, "empty")

        new_df = _normalize_df(result.to_dataframe(), ticker)
        if new_df is None:
            return (ticker, False, "empty_df")

        if full or cached_end is None:
            merged = new_df
        else:
            old_df = _load_cached_df(ticker)
            old_norm = _normalize_df(old_df, ticker) if old_df is not None else None
            merged = (
                pd.concat([old_norm, new_df], ignore_index=True)
                .sort_values("timestamps")
                .drop_duplicates(subset=["timestamps"], keep="last")
                .reset_index(drop=True)
                if old_norm is not None and len(old_norm) > 0
                else new_df
            )

        # 增量窗口保护：provider（akshare stock_hk_daily）忽略 start_date 返回全量历史，
        # 正常增量下 new_df 只有「缓存末日之后」的少数几行。若返回行数远超窗口，
        # 说明源口径漂移（全历史 / 异常长片段），直接写库会把 end 推到不可信的未来。
        # 窗口上限 31 天：覆盖长假、补同步、长停牌恢复；容忍带 +10 行吸收停牌日的稀疏返回。
        if (not full) and cached_end is not None:
            window_days = (pd.Timestamp(end_date) - pd.Timestamp(cached_end)).days
            if 0 < window_days <= 31 and len(new_df) > window_days + 10:
                logger.warning(
                    f"{ticker}: 增量窗口 {window_days}d 但返回 {len(new_df)} 行，"
                    "疑似源数据异常，跳过本次更新"
                )
                return (ticker, False, f"suspicious_window(rows={len(new_df)})")

        _write_df(ticker, merged)
        return (ticker, True, f"rows={len(merged)} +new={len(new_df)}")

    except Exception as e:  # 单只失败不阻断整体
        return (ticker, False, f"{type(e).__name__}: {e}"[:120])


def main() -> int:
    """同步港股 K 线。

    Returns:
        进程退出码（0 成功，1 全部失败或无数据）。
    """
    parser = argparse.ArgumentParser(description="港股 K 线同步（默认增量，--full 全量）")
    parser.add_argument(
        "--start", default=START_DATE, help=f"全量模式起始日期（默认 {START_DATE}）"
    )
    parser.add_argument("--end", default=None, help="结束日期（默认最近交易日）")
    parser.add_argument("--codes", default=None, help="指定代码，逗号分隔（不含 hk. 前缀）")
    parser.add_argument("--full", action="store_true", help="全量重建（忽略既有缓存）")
    parser.add_argument("--force", action="store_true", help="强制刷新选中的票（增量窗口）")
    parser.add_argument("--dry-run", action="store_true", help="仅打印计划")
    parser.add_argument("--workers", type=int, default=WORKERS, help=f"并发数（默认 {WORKERS}）")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 只（0=全部）")
    args = parser.parse_args()

    _load_env()

    end_date = args.end or expected_trading_day()
    logger.info(f"🚀 港股 K 线同步启动  目标日期 {end_date}  模式={'全量' if args.full else '增量'}")

    if args.codes:
        codes = [c.strip().replace("hk.", "") for c in args.codes.split(",") if c.strip()]
        tickers = [f"hk.{c.zfill(5)}" for c in codes]
    else:
        tickers = get_hk_ticker_list()
        if not tickers:
            logger.error("❌ 无法获取港股列表，退出")
            return 1

    cached_ends = get_cached_hk_ends()

    if args.full or args.force:
        need_fetch = tickers
    else:
        need_fetch = [t for t in tickers if cached_ends.get(t, "") < end_date]

    if args.limit > 0:
        need_fetch = need_fetch[: args.limit]

    logger.info(f"  已缓存 {len(cached_ends)} 只；本次需拉取 {len(need_fetch)} 只")
    if not need_fetch:
        logger.info("✅ 全部港股均已更新至目标日期，无需拉取")
        return 0

    if args.dry_run:
        logger.info("🧪 DRY-RUN：仅打印计划")
        for t in need_fetch[:50]:
            logger.info(f"  {t}  缓存至 {cached_ends.get(t, '无')}")
        if len(need_fetch) > 50:
            logger.info(f"  ... 其余 {len(need_fetch) - 50} 只省略")
        return 0

    factory = get_data_factory()
    if factory.get_provider(HK_PROVIDER) is None:
        logger.error(f"❌ {HK_PROVIDER} provider 不可用，退出")
        return 1

    total_ok = total_fail = total_skip = 0
    batch_size = 50

    for batch_start in range(0, len(need_fetch), batch_size):
        batch = need_fetch[batch_start : batch_start + batch_size]
        logger.info(f"📦 批次 [{batch_start + 1}~{batch_start + len(batch)}/{len(need_fetch)}]")

        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
            futures = {
                executor.submit(
                    fetch_and_store_hk,
                    factory,
                    t,
                    args.start,
                    end_date,
                    args.full,
                    cached_ends.get(t),
                ): t
                for t in batch
            }
            for future in as_completed(futures):
                ticker = futures[future]
                try:
                    _, success, detail = future.result()
                except Exception as e:  # 线程内未捕获异常兜底
                    total_fail += 1
                    logger.warning(f"  ❌ {ticker}: 异常 {e}")
                    continue
                if success and detail == "already_up_to_date":
                    total_skip += 1
                elif success:
                    total_ok += 1
                    logger.debug(f"  ✅ {ticker}: {detail}")
                else:
                    total_fail += 1
                    logger.warning(f"  ❌ {ticker}: {detail}")

        if batch_start + batch_size < len(need_fetch):
            time.sleep(2)

    logger.info("=" * 60)
    logger.info(
        f"✅ 港股同步完成：更新 {total_ok}，已最新 {total_skip}，失败 {total_fail}，"
        f"总计 {len(need_fetch)}"
    )
    logger.info("=" * 60)
    return 0 if total_ok > 0 or total_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
