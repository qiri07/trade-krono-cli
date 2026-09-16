#!/usr/bin/env python3
"""数据同步管理器 — 统一的完整性检查、备份、拉取、验证流程。

提供三个核心命令：
  - sync:full-history  全量历史数据同步（2015年起）
  - sync:daily         每日增量同步（补齐今日数据）
  - sync:verify        数据完整性检查

用法：
  uv run python scripts/sync_manager.py full-history
  uv run python scripts/sync_manager.py daily
  uv run python scripts/sync_manager.py verify
"""

from __future__ import annotations

import shutil
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
from loguru import logger

from scripts._utils import CACHE_DB, get_provider_chain
from trade_krono_cli.cache import get_cache
from trade_krono_cli.cli_commands.core import _load_env
from trade_krono_cli.data_providers.factory import get_data_factory

# ── 配置 ───────────────────────────────────────────────────────────────
DEFAULT_START = "2015-01-01"
DEFAULT_END = datetime.now().strftime("%Y-%m-%d")
BATCH_SIZE = 100
WORKERS = 8
FETCH_TIMEOUT = 60
BACKUP_DIR = Path("outputs/cache/backups")


# ── 工具函数 ───────────────────────────────────────────────────────────


def _now_str() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def create_backup() -> Path:
    """创建当前数据库备份，返回备份路径。"""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = _now_str()
    backup_path = BACKUP_DIR / f"pipeline_cache_backup_{ts}.db"
    shutil.copy2(CACHE_DB, backup_path)
    size_mb = backup_path.stat().st_size / 1024 / 1024
    logger.info(f"💾 备份已创建: {backup_path.name} ({size_mb:.0f}MB)")
    return backup_path


def verify_backup(backup_path: Path | None = None) -> dict[str, Any]:
    """验证备份数据库的完整性。"""
    if backup_path is None:
        # 找最近的备份
        backups = sorted(BACKUP_DIR.glob("pipeline_cache_backup_*.db"), reverse=True)
        if not backups:
            return {"valid": False, "error": "无备份文件"}
        backup_path = backups[0]

    try:
        conn = sqlite3.connect(str(backup_path))
        cnt = conn.execute("SELECT COUNT(DISTINCT ticker) FROM kline_cache").fetchone()[0]
        size_mb = backup_path.stat().st_size / 1024 / 1024
        conn.close()
        logger.info(f"✅ 备份验证通过: {cnt} 只股票, {size_mb:.0f}MB")
        return {"valid": True, "tickers": cnt, "size_mb": size_mb}
    except Exception as e:
        logger.error(f"❌ 备份验证失败: {e}")
        return {"valid": False, "error": str(e)}


def analyze_gaps(start_date: str = DEFAULT_START, end_date: str = DEFAULT_END) -> dict[str, Any]:
    """分析当前数据缺口。"""
    conn = sqlite3.connect(str(CACHE_DB))
    cur = conn.cursor()

    total = cur.execute("SELECT COUNT(DISTINCT ticker) FROM kline_cache").fetchone()[0]
    min_date = cur.execute("SELECT MIN(start) FROM kline_cache").fetchone()[0]
    max_date = cur.execute("SELECT MAX(end) FROM kline_cache").fetchone()[0]

    # 今日覆盖
    today = datetime.now().strftime("%Y-%m-%d")
    today_count = cur.execute(
        f"SELECT COUNT(DISTINCT ticker) FROM kline_cache WHERE end >= '{today}'"
    ).fetchone()[0]

    # 短序列（<100行）— 直接查DB中的end字段，避免pickle解析
    short = cur.execute("""
        SELECT ticker FROM kline_cache
        WHERE (SELECT COUNT(*) FROM kline_cache k2 WHERE k2.ticker=kline_cache.ticker) < 100
          AND ticker NOT LIKE 'bj.%'
        ORDER BY ticker
    """).fetchall()

    # 缺失今日数据的非北交所股票
    missing_today = cur.execute(f"""
        SELECT ticker FROM kline_cache
        WHERE end < '{today}'
          AND ticker NOT LIKE 'bj.%'
        ORDER BY ticker
    """).fetchall()

    # 重复检查
    dups = cur.execute("""
        SELECT ticker, COUNT(*) as cnt FROM kline_cache
        GROUP BY ticker HAVING cnt > 1
    """).fetchall()

    conn.close()

    logger.info("📊 数据缺口分析:")
    logger.info(f"  总股票: {total} 只")
    logger.info(f"  数据范围: {min_date} ~ {max_date}")
    logger.info(f"  今日覆盖: {today_count}/{total} ({today_count * 100 // max(total, 1)}%)")
    logger.info(f"  短序列(<100行,非北交所): {len(short)} 只")
    logger.info(f"  缺今日数据(非北交所): {len(missing_today)} 只")
    logger.info(f"  重复股票: {len(dups)} 只")

    return {
        "total": total,
        "min_date": min_date,
        "max_date": max_date,
        "today_count": today_count,
        "today_pct": today_count * 100 // max(total, 1),
        "short_count": len(short),
        "missing_today": len(missing_today),
        "dup_count": len(dups),
        "short_tickers": [r[0] for r in short],
        "missing_tickers": [r[0] for r in missing_today],
    }


def fetch_and_store(
    factory, ticker: str, provider_chain: list[str], start_date: str, end_date: str
) -> tuple[str, int, str]:
    """拉取数据并通过 cache API 存储（自动去重合并）。"""
    try:
        # 拆分为最多10年的子区间（同花顺 API 限制）
        sub_ranges: list[tuple[str, str]] = []
        cur = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")
        while cur <= end:
            chunk_end = min(cur + timedelta(days=3650), end)
            sub_ranges.append((cur.strftime("%Y-%m-%d"), chunk_end.strftime("%Y-%m-%d")))
            cur = chunk_end + timedelta(days=1)

        # baostock 优先用于2015年前数据
        fallback_chain = ["baostock"] + [p for p in provider_chain if p != "baostock"]

        merged_df: pd.DataFrame | None = None
        for sub_start, sub_end in sub_ranges:
            for provider_name in fallback_chain:
                try:
                    provider = factory.get_provider(provider_name)
                    if provider is None:
                        continue
                    result = provider.fetch_kline(
                        ticker, sub_start, sub_end, frequency="d", adjustflag="1"
                    )
                    if result is None or result.is_empty:
                        continue
                    df = result.to_dataframe()
                    if df is None or len(df) == 0:
                        continue
                    if merged_df is None:
                        merged_df = df
                    else:
                        merged_df = pd.concat([merged_df, df], ignore_index=True)
                    break
                except Exception:
                    continue
            if merged_df is not None and len(merged_df) > 0:
                break

        if merged_df is None or len(merged_df) == 0:
            return (ticker, 0, "NO_DATA")

        # 去重并排序
        ts_col = "timestamps" if "timestamps" in merged_df.columns else "date"
        merged_df = merged_df.drop_duplicates(subset=[ts_col]).reset_index(drop=True)
        ts = pd.to_datetime(merged_df[ts_col])

        # 使用 cache API 存储（自动处理去重合并）
        cache = get_cache()
        cache.set_kline(
            ticker,
            ts.min().strftime("%Y-%m-%d"),
            ts.max().strftime("%Y-%m-%d"),
            freq="d",
            df=merged_df,
            ttl=0.0,
            adjustflag="1",
        )
        return (
            ticker,
            len(merged_df),
            f"{ts.min().strftime('%Y-%m-%d')}~{ts.max().strftime('%Y-%m-%d')}",
        )

    except Exception as e:
        return (ticker, 0, f"ERROR: {str(e)[:50]}")


def verify_data() -> dict[str, Any]:
    """验证最终数据质量。"""
    import io

    conn = sqlite3.connect(str(CACHE_DB))
    all_data = conn.execute("SELECT ticker, data FROM kline_cache").fetchall()
    conn.close()

    valid = 0
    corrupted = 0
    short_list: list[tuple[str, int]] = []
    today_count = 0

    for ticker, data in all_data:
        try:
            df = pd.read_pickle(io.BytesIO(data))
            if len(df) < 50:
                short_list.append((ticker, len(df)))
            latest = pd.to_datetime(df["timestamps"].max()).date()
            if latest >= datetime.now().date():
                today_count += 1
            valid += 1
        except Exception:
            corrupted += 1

    total = len(all_data)
    logger.info("\n📊 数据验证结果:")
    logger.info(f"  总记录: {total} 只")
    logger.info(f"  有效: {valid} 只")
    logger.info(f"  损坏: {corrupted} 只")
    logger.info(f"  今日覆盖: {today_count}/{total} ({today_count * 100 // max(total, 1)}%)")
    if short_list:
        logger.info(f"  短序列(<50行): {len(short_list)} 只")

    return {
        "total": total,
        "valid": valid,
        "corrupted": corrupted,
        "today_count": today_count,
        "today_pct": today_count * 100 // max(total, 1),
        "short_count": len(short_list),
    }


# ── 命令实现 ───────────────────────────────────────────────────────────


def cmd_full_history(start_date: str, end_date: str) -> None:
    """全量历史数据同步（2015年起）。"""
    logger.info(f"🚀 启动全量历史同步: {start_date} ~ {end_date}")

    # 1. 前置检查
    gaps = analyze_gaps(start_date, end_date)
    if gaps["total"] < 100:
        logger.warning("⚠️  数据量异常少，先创建备份再恢复")
        create_backup()
    else:
        create_backup()

    # 2. 确定需要处理的股票
    conn = sqlite3.connect(str(CACHE_DB))
    # 需要补全历史（start >= start_date）的股票
    need_update = [
        r[0]
        for r in conn.execute(
            "SELECT ticker FROM kline_cache WHERE start >= ? AND start <= ?",
            (start_date, end_date),
        ).fetchall()
    ]
    # 已有完整历史的也重新验证
    already_full = [
        r[0]
        for r in conn.execute(
            "SELECT ticker FROM kline_cache WHERE start < ?", (start_date,)
        ).fetchall()
    ]
    conn.close()

    # 合并处理列表（去重）
    all_to_process = list(dict.fromkeys(already_full + need_update))
    logger.info(f"📌 需处理: {len(all_to_process)} 只（含已覆盖 {len(already_full)} 只）")

    if not all_to_process:
        logger.info("✅ 所有股票历史已完整，无需同步")
        return

    # 3. 执行同步
    factory = get_data_factory()
    success = 0
    no_data = 0
    failed = []
    start_time = time.time()

    for batch_start in range(0, len(all_to_process), BATCH_SIZE):
        batch = all_to_process[batch_start : batch_start + BATCH_SIZE]
        batch_num = batch_start // BATCH_SIZE + 1
        logger.info(
            f"📦 批次 {batch_num} [{batch_start + 1}~{batch_start + len(batch)}/{len(all_to_process)}]"
        )

        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = {
                pool.submit(
                    fetch_and_store, factory, t, get_provider_chain(t), start_date, end_date
                ): t
                for t in batch
            }
            for future in as_completed(futures):
                ticker, n_rows, status = future.result()
                if n_rows > 0:
                    success += 1
                    logger.info(f"  ✅ {ticker} +{n_rows}行 ({status})")
                elif status == "NO_DATA":
                    no_data += 1
                    logger.warning(f"  ⚠️  {ticker} 无数据 ({status})")
                else:
                    failed.append(ticker)
                    logger.warning(f"  ❌ {ticker} ({status})")

        time.sleep(0.5)

    elapsed = time.time() - start_time
    logger.info(f"\n{'=' * 60}")
    logger.info("✅ 全量历史同步完成!")
    logger.info(f"  成功: {success}, 无数据: {no_data}, 失败: {len(failed)}")
    logger.info(f"  耗时: {elapsed:.1f}s ({elapsed / 60:.1f}min)")
    if failed:
        logger.warning(f"  失败股票 ({len(failed)}): {', '.join(failed[:20])}")

    # 4. 验证
    logger.info("\n🔍 同步后验证...")
    verify_data()


def cmd_daily() -> None:
    """每日增量同步。"""
    today = datetime.now().strftime("%Y-%m-%d")
    logger.info(f"🚀 启动每日增量同步: {today}")

    # 1. 前置检查
    analyze_gaps(end_date=today)
    create_backup()

    # 2. 获取需要补今日数据的股票
    conn = sqlite3.connect(str(CACHE_DB))
    need_today = [
        r[0]
        for r in conn.execute(f"""
            SELECT DISTINCT ticker FROM kline_cache
            WHERE end < '{today}'
              AND ticker NOT LIKE 'bj.%'
            ORDER BY ticker
        """).fetchall()
    ]
    conn.close()
    logger.info(f"📌 需要补今日数据的股票: {len(need_today)} 只（非北交所）")

    if not need_today:
        logger.info("✅ 今日数据已完整，无需同步")
        return

    # 3. 执行同步
    factory = get_data_factory()
    success = 0
    failed = []
    start_time = time.time()

    for batch_start in range(0, len(need_today), BATCH_SIZE):
        batch = need_today[batch_start : batch_start + BATCH_SIZE]
        batch_num = batch_start // BATCH_SIZE + 1
        logger.info(
            f"📦 批次 {batch_num} [{batch_start + 1}~{batch_start + len(batch)}/{len(need_today)}]"
        )

        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = {
                pool.submit(fetch_and_store, factory, t, get_provider_chain(t), today, today): t
                for t in batch
            }
            for future in as_completed(futures):
                ticker, n_rows, status = future.result()
                if n_rows > 0:
                    success += 1
                else:
                    failed.append(ticker)
        time.sleep(0.5)

    elapsed = time.time() - start_time
    logger.info(f"\n{'=' * 60}")
    logger.info("✅ 每日增量同步完成!")
    logger.info(f"  成功: {success}, 失败: {len(failed)}")
    logger.info(f"  耗时: {elapsed:.1f}s")
    if failed:
        logger.warning(f"  失败股票 ({len(failed)}): {', '.join(failed[:20])}")

    # 4. 验证
    logger.info("\n🔍 同步后验证...")
    verify_data()


def cmd_verify() -> None:
    """数据完整性检查。"""
    logger.info("🔍 启动数据完整性检查...")
    analyze_gaps()
    result = verify_data()

    logger.info(f"\n{'=' * 60}")
    logger.info("📊 数据完整性检查报告")
    logger.info(f"{'=' * 60}")
    logger.info(f"  总股票:     {result['total']} 只")
    logger.info(
        f"  有效数据:   {result['valid']} 只 ({result['valid'] * 100 // max(result['total'], 1)}%)"
    )
    logger.info(f"  损坏数据:   {result['corrupted']} 只")
    logger.info(f"  短序列:     {result['short_count']} 只")
    logger.info(f"  今日覆盖:   {result['today_count']}/{result['total']} ({result['today_pct']}%)")
    logger.info(f"{'=' * 60}")

    if result["corrupted"] > 0:
        logger.warning(f"⚠️  发现 {result['corrupted']} 只损坏数据，建议恢复备份")
    elif result["today_pct"] < 95:
        logger.warning(f"⚠️  今日覆盖仅 {result['today_pct']}%，建议运行 daily sync")
    else:
        logger.info("✅ 数据质量良好")


# ── 入口 ───────────────────────────────────────────────────────────────


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="数据同步管理器")
    sub = parser.add_subparsers(dest="command", required=True)

    # full-history
    p_fh = sub.add_parser("full-history", help="全量历史同步（2015年起）")
    p_fh.add_argument("--start", default=DEFAULT_START, help="起始日期")
    p_fh.add_argument("--end", default=DEFAULT_END, help="结束日期")

    # daily
    sub.add_parser("daily", help="每日增量同步")

    # verify
    sub.add_parser("verify", help="数据完整性检查")

    args = parser.parse_args()

    _load_env()

    if args.command == "full-history":
        cmd_full_history(args.start, args.end)
    elif args.command == "daily":
        cmd_daily()
    elif args.command == "verify":
        cmd_verify()


if __name__ == "__main__":
    main()
