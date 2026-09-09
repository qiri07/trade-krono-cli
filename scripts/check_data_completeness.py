#!/usr/bin/env python3
"""检查 K 线缓存数据的完整性和每日覆盖情况。"""
from __future__ import annotations

import pickle
import sqlite3
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

DB_PATH = Path("outputs/cache/pipeline_cache.db")
TARGET_DATE = "2026-09-09"


def load_db_rows() -> list[sqlite3.Row]:
    """从数据库加载所有缓存记录。"""
    if not DB_PATH.exists():
        print(f"数据库不存在: {DB_PATH}")
        return []
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT ticker, start, end, data FROM kline_cache").fetchall()
    conn.close()
    return rows


def parse_ticker_dates(data_bytes: bytes) -> tuple[list[str], bool]:
    """解析 K 线数据，返回日期列表和是否成功。"""
    try:
        df = pickle.loads(data_bytes)
    except Exception:
        return [], False
    if "timestamps" not in df.columns:
        return [], False
    try:
        ts = pd.to_datetime(df["timestamps"]).dt.normalize()
        return sorted(set(ts.dt.strftime("%Y-%m-%d").tolist())), True
    except Exception:
        return [], False


def analyze_ticker(
    row: sqlite3.Row,
    end_counter: Counter[str],
    actual_end_counter: Counter[str],
    recent_coverage: dict[str, int],
    gap_details: dict[str, list[str]],
    parse_errors: list[str],
    start_mismatches: list[tuple[str, str, str]],
) -> None:
    """分析单只股票的数据完整性。"""
    ticker = row["ticker"]
    db_start = row["start"]
    db_end = row["end"]
    end_counter[db_end] += 1

    data_bytes = row["data"]
    if not data_bytes:
        parse_errors.append(ticker)
        return

    dates, ok = parse_ticker_dates(data_bytes)
    if not ok:
        parse_errors.append(ticker)
        return

    if not dates:
        parse_errors.append(ticker)
        return

    actual_end = dates[-1]
    actual_start = dates[0]
    actual_end_counter[actual_end] += 1

    if actual_start != db_start:
        start_mismatches.append((ticker, actual_start, db_start))

    _update_recent_coverage(dates, recent_coverage)
    _check_gaps(ticker, dates, db_start, gap_details)


def _update_recent_coverage(dates: list[str], coverage: dict[str, int]) -> None:
    """更新最近10个交易日的覆盖统计。"""
    today = datetime.now()
    checked: set[str] = set()
    for i in range(10):
        d = today - timedelta(days=i)
        # 回退到上一个工作日
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        ds = d.strftime("%Y-%m-%d")
        if ds not in checked:
            checked.add(ds)
            if ds not in coverage:
                coverage[ds] = 0
            if ds in dates:
                coverage[ds] += 1


def _check_gaps(
    ticker: str,
    dates: list[str],
    db_start: str,
    gap_details: dict[str, list[str]],
) -> None:
    """检查目标日期范围内的数据缺口。"""
    target_dt = datetime.strptime(TARGET_DATE, "%Y-%m-%d")
    check_start = max(
        target_dt - timedelta(days=10),
        datetime.strptime(db_start, "%Y-%m-%d"),
    )
    missing: list[str] = []
    d = check_start
    while d <= target_dt:
        if d.weekday() < 5:
            ds = d.strftime("%Y-%m-%d")
            if ds not in dates:
                missing.append(ds)
        d += timedelta(days=1)
    if missing:
        gap_details[ticker] = missing


def _print_header() -> None:
    """打印报告标题。"""
    print("=" * 60)
    print(f"📊 K 线缓存完整性报告  (目标日期: {TARGET_DATE})")
    print("=" * 60)


def _print_end_distribution(end_counter: Counter[str], actual_end_counter: Counter[str]) -> None:
    """打印 end 日期分布。"""
    print("\n【数据库 end 字段分布】")
    for date_str in sorted(end_counter.keys(), reverse=True):
        print(f"  end={date_str}: {end_counter[date_str]} 只")

    print("\n【实际数据 end 分布】")
    for date_str in sorted(actual_end_counter.keys(), reverse=True):
        print(f"  actual_end={date_str}: {actual_end_counter[date_str]} 只")


def _print_status(total: int, end_counter: Counter[str]) -> None:
    """打印达标状态。"""
    up_to_date = end_counter.get(TARGET_DATE, 0) + sum(
        v for k, v in end_counter.items() if k > TARGET_DATE
    )
    stale = total - up_to_date
    print("\n【达标状态】")
    print(f"  ✅ 达标 (end >= {TARGET_DATE}): {up_to_date} 只 ({up_to_date*100//total}%)")
    print(f"  ❌ 滞后 (end < {TARGET_DATE}):  {stale} 只 ({stale*100//total}%)")


def _print_recent_coverage(recent_coverage: dict[str, int], total: int) -> None:
    """打印最近10个交易日覆盖情况。"""
    print("\n【最近10个交易日覆盖情况】")
    today = datetime.now()
    for i in range(10):
        d = today - timedelta(days=i)
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        ds = d.strftime("%Y-%m-%d")
        cnt = recent_coverage.get(ds, 0)
        pct = cnt * 100 // total
        bar = "█" * (cnt // 30)
        marker = " ✅" if ds == TARGET_DATE else ""
        print(f"  {ds}: {cnt:4d}/{total} ({pct:3d}%) {bar}{marker}")


def _print_errors(parse_errors: list[str], start_mismatches: list[tuple[str, str, str]]) -> None:
    """打印错误信息。"""
    if parse_errors:
        print(f"\n❌ 解析错误: {len(parse_errors)} 只")
        for t in parse_errors[:5]:
            print(f"   - {t}")

    if start_mismatches:
        print(f"\n⚠️  DB start 与实际不符: {len(start_mismatches)} 只")
        for t, actual, db in start_mismatches[:5]:
            print(f"   - {t}: 实际={actual}, DB记录={db}")


def _print_stale_stocks(stale: int, end_counter: Counter[str]) -> None:
    """打印滞后股票统计。"""
    if stale > 0:
        print(f"\n【滞后股票分析】(end < {TARGET_DATE})")
        stale_by_end: Counter[str] = Counter()
        for date_str, cnt in end_counter.items():
            if date_str < TARGET_DATE:
                stale_by_end[date_str] = cnt
        for date_str, cnt in sorted(stale_by_end.items(), reverse=True):
            print(f"  end={date_str}: {cnt} 只")


def _print_gaps(gap_details: dict[str, list[str]]) -> None:
    """打印数据缺口信息。"""
    if gap_details:
        print(f"\n【数据缺口】({len(gap_details)} 只)")
        for t, gaps in list(gap_details.items())[:5]:
            print(f"  ❌ {t}: 缺失 {len(gaps)} 个交易日")


def print_report(
    rows: list[sqlite3.Row],
    end_counter: Counter[str],
    actual_end_counter: Counter[str],
    recent_coverage: dict[str, int],
    gap_details: dict[str, list[str]],
    parse_errors: list[str],
    start_mismatches: list[tuple[str, str, str]],
) -> None:
    """打印完整性报告。"""
    _print_header()
    _print_end_distribution(end_counter, actual_end_counter)

    total = len(rows)
    up_to_date = end_counter.get(TARGET_DATE, 0) + sum(
        v for k, v in end_counter.items() if k > TARGET_DATE
    )
    stale = total - up_to_date

    _print_status(total, end_counter)
    _print_recent_coverage(recent_coverage, total)
    _print_errors(parse_errors, start_mismatches)
    _print_stale_stocks(stale, end_counter)
    _print_gaps(gap_details)

    print("\n" + "=" * 60)


def main() -> None:
    """主入口。"""
    rows = load_db_rows()
    if not rows:
        return

    print(f"总股票数: {len(rows)}\n")

    end_counter: Counter[str] = Counter()
    actual_end_counter: Counter[str] = Counter()
    recent_coverage: dict[str, int] = {}
    gap_details: dict[str, list[str]] = {}
    parse_errors: list[str] = []
    start_mismatches: list[tuple[str, str, str]] = []

    for row in rows:
        analyze_ticker(
            row,
            end_counter,
            actual_end_counter,
            recent_coverage,
            gap_details,
            parse_errors,
            start_mismatches,
        )

    print_report(
        rows,
        end_counter,
        actual_end_counter,
        recent_coverage,
        gap_details,
        parse_errors,
        start_mismatches,
    )


if __name__ == "__main__":
    main()
