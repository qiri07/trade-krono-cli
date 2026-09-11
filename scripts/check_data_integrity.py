#!/usr/bin/env python3
"""数据完整性检查脚本 — 使用正确的pandas pickle格式解码"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from io import BytesIO
from pathlib import Path

import pandas as pd

DB_PATH = Path("outputs/cache/pipeline_cache.db")
BACKUP_DIR = Path("outputs/cache/backups")


def decode_data(raw: bytes) -> tuple[int, str, str]:
    """解码kline数据，返回(记录数, 首日期, 末日期)"""
    try:
        df = pd.read_pickle(BytesIO(raw))
        if len(df) == 0:
            return 0, "", ""
        # 获取日期范围
        if "timestamps" in df.columns:
            first = str(df["timestamps"].iloc[0])[:10]
            last = str(df["timestamps"].iloc[-1])[:10]
        elif len(df.columns) > 0:
            first_col = df.columns[0]
            first = str(df[first_col].iloc[0])[:10]
            last = str(df[first_col].iloc[-1])[:10]
        else:
            first, last = "", ""
        return len(df), first, last
    except Exception:
        return -1, "", ""


def main() -> None:
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    print("=" * 65)
    print("📊 数据完整性检查报告")
    print(f"   检查时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 65)
    print()

    # ── 1. 基础统计 ──────────────────────────────────────────
    print("【1. 基础统计】")
    cur.execute("SELECT COUNT(*), COUNT(DISTINCT ticker) FROM kline_cache")
    total_records, total_tickers = cur.fetchone()
    cur.execute("SELECT MIN(start), MAX(end) FROM kline_cache")
    min_start, max_end = cur.fetchone()
    cur.execute("""
        SELECT COUNT(DISTINCT ticker) FROM kline_cache
        WHERE start <= '2020-12-31' AND end >= '2020-01-01'
    """)
    has_2020 = cur.fetchone()[0]
    print(f"  总记录数:   {total_records}")
    print(f"  股票数:     {total_tickers}")
    print(f"  数据范围:   {min_start} ~ {max_end}")
    print(f"  2020年数据: {has_2020} 只 ({has_2020 * 100 / total_tickers:.1f}%)")
    print()

    # ── 2. 重复检查 ──────────────────────────────────────────
    print("【2. 重复检查】")
    cur.execute("""
        SELECT ticker, COUNT(*) as cnt FROM kline_cache
        GROUP BY ticker HAVING cnt > 1
    """)
    dupes = cur.fetchall()
    if dupes:
        print(f"  ❌ 发现 {len(dupes)} 只股票有重复:")
        for row in dupes[:5]:
            print(f"     {row[0]}: {row[1]}条")
    else:
        print("  ✅ 无重复记录（每只股票恰好1条）")
    print()

    # ── 3. 空/小数据检查 ─────────────────────────────────────
    print("【3. 空/小数据检查】")
    cur.execute("SELECT COUNT(*) FROM kline_cache WHERE data IS NULL OR LENGTH(data)=0")
    empty = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM kline_cache WHERE LENGTH(data) < 500")
    tiny = cur.fetchone()[0]
    print(f"  NULL/空数据:   {empty} 条")
    print(f"  数据<500字节:  {tiny} 条", end="")
    if tiny > 0:
        cur.execute("SELECT ticker, LENGTH(data) FROM kline_cache WHERE LENGTH(data) < 500 LIMIT 5")
        for t, sz in cur.fetchall():
            print(f"\n     {t}: {sz} bytes")
    else:
        print()
    print()

    # ── 4. 各年份覆盖率 ──────────────────────────────────────
    print("【4. 各年份数据覆盖率】")
    for year in range(2020, 2027):
        cur.execute(
            """
            SELECT COUNT(DISTINCT ticker) FROM kline_cache
            WHERE end >= ? AND start <= ?
        """,
            (f"{year}-01-01", f"{year}-12-31"),
        )
        cnt = cur.fetchone()[0]
        pct = cnt * 100 / total_tickers
        bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
        print(f"  {year}年: {cnt:5d}/{total_tickers} ({pct:5.1f}%) {bar}")
    print()

    # ── 5. 股票类型分布 ──────────────────────────────────────
    print("【5. 股票类型分布】")
    cur.execute("""
        SELECT
            CASE
                WHEN ticker LIKE 'bj.%' THEN '北交所'
                WHEN ticker LIKE 'sh.68%' THEN '科创板'
                WHEN ticker LIKE 'sz.30%' THEN '创业板'
                WHEN ticker LIKE 'sh.60%' THEN '上海主板'
                WHEN ticker LIKE 'sz.00%' THEN '深圳主板'
                ELSE '其他'
            END as typ, COUNT(*) as cnt
        FROM kline_cache GROUP BY typ ORDER BY cnt DESC
    """)
    for row in cur.fetchall():
        print(f"  {row[0]}: {row[1]} 只")
    print()

    # ── 6. 记录数分布（估算数据质量） ────────────────────────
    print("【6. K线记录数分布（通过数据大小估算）】")
    cur.execute("""
        SELECT
            CASE
                WHEN LENGTH(data) < 5000 THEN '< 5KB (异常?)'
                WHEN LENGTH(data) < 20000 THEN '5-20KB (~25-100条)'
                WHEN LENGTH(data) < 50000 THEN '20-50KB (~100-250条)'
                WHEN LENGTH(data) < 100000 THEN '50-100KB (~250-500条)'
                ELSE '> 100KB (>500条)'
            END as size_range, COUNT(*) as cnt
        FROM kline_cache GROUP BY size_range ORDER BY size_range
    """)
    for row in cur.fetchall():
        print(f"  {row[0]}: {row[1]} 只")
    print()

    # ── 7. 短跨度/特殊状态股票 ───────────────────────────────
    print("【7. 短跨度股票 TOP15】（新上市/停牌/退市/数据异常）")
    cur.execute("""
        SELECT ticker, start, end,
               CAST(julianday(end) - julianday(start) AS INTEGER) as span_days,
               LENGTH(data) as data_len
        FROM kline_cache
        ORDER BY span_days ASC, data_len ASC
        LIMIT 15
    """)
    short_rows = cur.fetchall()
    if short_rows:
        for row in short_rows:
            print(f"  {row[0]}: {row[1]} ~ {row[2]} ({row[3]}天), data={row[4]}B")
    print()

    # ── 8. 数据解码验证（随机20只） ──────────────────────────
    print("【8. 数据解码验证（随机20只股票）】")
    cur.execute("SELECT ticker, start, end, data FROM kline_cache ORDER BY RANDOM() LIMIT 20")
    valid = 0
    invalid = 0
    for row in cur.fetchall():
        ticker, db_start, db_end, raw = row
        n_records, first_date, last_date = decode_data(raw)
        if n_records > 0:
            print(
                f"  ✅ {ticker}: {db_start}~{db_end}, {n_records}条K线, 首:{first_date}, 末:{last_date}"
            )
            valid += 1
        else:
            print(f"  ❌ {ticker}: 解码失败")
            invalid += 1
    print(f"  解码成功: {valid}/20, 失败: {invalid}/20")
    print()

    # ── 9. 关键股票抽检 ──────────────────────────────────────
    print("【9. 关键股票完整校验】")
    key_tickers = ["sh.600519", "sz.000858", "sh.601318", "sz.000001", "sh.600000", "sh.601398"]
    for ticker in key_tickers:
        cur.execute("SELECT ticker, start, end, data FROM kline_cache WHERE ticker=?", (ticker,))
        row = cur.fetchone()
        if not row:
            print(f"  ❌ {ticker}: 无记录")
            continue
        _, start, end, raw = row
        n_records, first_date, last_date = decode_data(raw)
        if n_records > 0:
            # 检查数据是否跨越整个范围
            span_ok = (first_date <= start <= last_date) and (first_date <= end <= last_date)
            status = "✅" if span_ok else "⚠️"
            print(
                f"  {status} {ticker}: {start}~{end}, {n_records}条K线, 实际覆盖{first_date}~{last_date}"
            )
        else:
            print(f"  ❌ {ticker}: 解码失败")
    print()

    # ── 10. 备份状态 ─────────────────────────────────────────
    print("【10. 备份文件状态】")
    backups = sorted(BACKUP_DIR.glob("pipeline_cache_*.db"))
    for bp in backups[-4:]:
        size_mb = bp.stat().st_size / 1024 / 1024
        mtime = datetime.fromtimestamp(bp.stat().st_mtime).strftime("%m-%d %H:%M")
        print(f"  {bp.name}: {size_mb:.1f} MB ({mtime})")
    print()

    # ── 11. 总结 ─────────────────────────────────────────────
    print("=" * 65)
    issues: list[str] = []
    warnings: list[str] = []

    if dupes:
        issues.append(f"重复记录 {len(dupes)} 只")
    if empty > 0:
        issues.append(f"空数据 {empty} 条")
    if tiny > 0:
        issues.append(f"过小数据 {tiny} 条")
    if total_records != total_tickers:
        issues.append(f"记录数({total_records})≠股票数({total_tickers})")
    if invalid > 0:
        issues.append(f"解码失败 {invalid}/20 只抽样")

    if has_2020 < total_tickers * 0.6:
        warnings.append(f"2020年数据覆盖仅 {has_2020 * 100 / total_tickers:.1f}%（新股多属正常）")
    if len(short_rows) > 100:
        warnings.append(f"{len(short_rows)} 只股票数据跨度<1天")

    if issues:
        print(f"❌ 发现问题: {'; '.join(issues)}")
    else:
        print("✅ 数据完整性良好，无重大问题")
    if warnings:
        print(f"⚠️  提示: {'; '.join(warnings)}")
    print("=" * 65)

    conn.close()


if __name__ == "__main__":
    main()
