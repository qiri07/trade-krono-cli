#!/usr/bin/env python3
"""检查 K 线缓存数据的完整性和每日覆盖情况（修正版）"""
from __future__ import annotations

import pickle
import sqlite3
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

DB_PATH = Path("outputs/cache/pipeline_cache.db")
TARGET_DATE = "2026-09-09"  # 期望的最新交易日


def main() -> None:
    if not DB_PATH.exists():
        print(f"数据库不存在: {DB_PATH}")
        return

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT ticker, start, end, data FROM kline_cache")
    rows = cur.fetchall()
    conn.close()

    print(f"总股票数: {len(rows)}\n")

    # 按 end 日期分组（来自 DB 记录）
    end_counter: Counter[str] = Counter()
    # 从实际数据提取日期范围
    actual_end_counter: Counter[str] = Counter()
    recent_coverage: dict[str, int] = {}  # date -> count
    gap_details: dict[str, list[str]] = {}
    parse_errors: list[str] = []
    start_mismatches: list[tuple[str, str, str]] = []

    for row in rows:
        ticker = row["ticker"]
        db_start = row["start"]
        db_end = row["end"]
        end_counter[db_end] += 1

        data_bytes = row["data"]
        if not data_bytes:
            parse_errors.append(ticker)
            continue
        try:
            df = pickle.loads(data_bytes)
        except Exception:
            parse_errors.append(ticker)
            continue

        if "timestamps" not in df.columns:
            parse_errors.append(ticker)
            continue

        try:
            ts = pd.to_datetime(df["timestamps"]).dt.normalize()
            unique_dates = sorted(set(ts.dt.strftime("%Y-%m-%d").tolist()))
        except Exception:
            parse_errors.append(ticker)
            continue

        actual_end = unique_dates[-1]
        actual_start = unique_dates[0]
        actual_end_counter[actual_end] += 1

        if actual_start != db_start:
            start_mismatches.append((ticker, actual_start, db_start))

        # 检查最近几个交易日是否覆盖
        today = datetime.now()
        for i in range(10):
            d = today - timedelta(days=i)
            while d.weekday() >= 5:
                d -= timedelta(days=1)
            ds = d.strftime("%Y-%m-%d")
            if ds not in recent_coverage:
                recent_coverage[ds] = 0
            if ds in unique_dates:
                recent_coverage[ds] += 1

        # 检查目标日期之后是否有缺失（仅检查最近5天内的缺口）
        target_dt = datetime.strptime(TARGET_DATE, "%Y-%m-%d")
        check_start = max(target_dt - timedelta(days=10), datetime.strptime(actual_start, "%Y-%m-%d"))
        missing_recent = []
        d = check_start
        while d <= target_dt:
            if d.weekday() < 5:
                ds = d.strftime("%Y-%m-%d")
                if ds not in unique_dates:
                    missing_recent.append(ds)
            d += timedelta(days=1)
        if missing_recent:
            gap_details[ticker] = missing_recent

    # ===== 输出报告 =====
    print("=" * 60)
    print(f"📊 K 线缓存完整性报告  (目标日期: {TARGET_DATE})")
    print("=" * 60)

    print("\n【数据库 end 字段分布】")
    for date_str in sorted(end_counter.keys(), reverse=True):
        print(f"  end={date_str}: {end_counter[date_str]} 只")

    print("\n【实际数据 end 分布】")
    for date_str in sorted(actual_end_counter.keys(), reverse=True):
        print(f"  actual_end={date_str}: {actual_end_counter[date_str]} 只")

    # 达标统计
    up_to_date = end_counter.get(TARGET_DATE, 0) + sum(
        v for k, v in end_counter.items() if k > TARGET_DATE
    )
    stale = len(rows) - up_to_date
    print("\n【达标状态】")
    print(f"  ✅ 达标 (end >= {TARGET_DATE}): {up_to_date} 只 ({up_to_date*100//len(rows)}%)")
    print(f"  ❌ 滞后 (end < {TARGET_DATE}):  {stale} 只 ({stale*100//len(rows)}%)")

    print("\n【最近10个交易日覆盖情况】")
    today = datetime.now()
    for i in range(10):
        d = today - timedelta(days=i)
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        ds = d.strftime("%Y-%m-%d")
        cnt = recent_coverage.get(ds, 0)
        pct = cnt * 100 // len(rows)
        bar = "█" * (cnt // 30)
        marker = " ✅" if ds == TARGET_DATE else ""
        print(f"  {ds}: {cnt:4d}/{len(rows)} ({pct:3d}%) {bar}{marker}")

    if parse_errors:
        print(f"\n❌ 解析错误: {len(parse_errors)} 只")
        for t in parse_errors[:5]:
            print(f"   - {t}")

    if start_mismatches:
        print(f"\n⚠️  DB start 与实际不符: {len(start_mismatches)} 只")
        for t, actual, db in start_mismatches[:5]:
            print(f"   - {t}: 实际={actual}, DB记录={db}")

    # 滞后股票统计
    if stale > 0:
        print(f"\n【滞后股票分析】(end < {TARGET_DATE})")
        stale_by_end: Counter[str] = Counter()
        for date_str, cnt in end_counter.items():
            if date_str < TARGET_DATE:
                stale_by_end[date_str] = cnt
        for date_str, cnt in sorted(stale_by_end.items(), reverse=True):
            print(f"  end={date_str}: {cnt} 只")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()
