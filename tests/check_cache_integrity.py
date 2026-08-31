#!/usr/bin/env python3
"""K线缓存完整性检查"""

from __future__ import annotations

import os
import pickle
import sqlite3
from collections import Counter

DB = "outputs/cache/pipeline_cache.db"
conn = sqlite3.connect(DB)

print("=" * 55)
print("       K线缓存完整性检查报告")
print("=" * 55)

# [1] 基础统计
total_rows = conn.execute("SELECT COUNT(*) FROM kline_cache").fetchone()[0]
unique_tickers = conn.execute("SELECT COUNT(DISTINCT ticker) FROM kline_cache").fetchone()[0]
unique_tf = conn.execute(
    "SELECT COUNT(*) FROM (SELECT DISTINCT ticker, freq FROM kline_cache)"
).fetchone()[0]
dupes = conn.execute(
    "SELECT ticker, freq, start, end, COUNT(*) as cnt FROM kline_cache "
    "GROUP BY ticker, freq, start, end HAVING cnt > 1"
).fetchall()
sh = conn.execute(
    "SELECT COUNT(DISTINCT ticker) FROM kline_cache WHERE ticker LIKE 'sh.%'"
).fetchone()[0]
sz = conn.execute(
    "SELECT COUNT(DISTINCT ticker) FROM kline_cache WHERE ticker LIKE 'sz.%'"
).fetchone()[0]
bj = conn.execute(
    "SELECT COUNT(DISTINCT ticker) FROM kline_cache WHERE ticker LIKE 'bj.%'"
).fetchone()[0]

print("\n[1] 基础统计")
print(f"  总行数:          {total_rows:,}")
print(f"  唯一股票数:       {unique_tickers}  (sh:{sh} sz:{sz} bj:{bj})")
print(f"  唯一(ticker,freq): {unique_tf}")
print(f"  重复段:           {len(dupes)}")

# [2] 损坏检查
print("\n[2] 数据损坏检查 (pickle 反序列化)")
corrupted = 0
valid = 0
for row in conn.execute("SELECT ticker, freq, data FROM kline_cache"):
    try:
        df = pickle.loads(row[2])
        if not hasattr(df, "columns") or len(df.columns) != 7 or df.empty:
            corrupted += 1
        else:
            valid += 1
    except Exception:
        corrupted += 1
print(f"  有效段: {valid:,}")
print(f"  损坏段: {corrupted}")

# [3] 重叠段
print("\n[3] 重叠段检查")
overlap = conn.execute("""
    SELECT a.ticker, a.freq, a.start, a.end, b.start, b.end
    FROM kline_cache a JOIN kline_cache b
      ON a.ticker=b.ticker AND a.freq=b.freq AND a.rowid < b.rowid
    WHERE a.end >= b.start AND a.start <= b.end
""").fetchall()
print(f"  重叠段: {len(overlap)}")
for o in overlap[:5]:
    print(f"    [{o[1]}] {o[0]}: {o[2]}~{o[3]} vs {o[4]}~{o[5]}")

# [4] start > end
print("\n[4] 日期顺序异常 (start > end)")
bad_order = conn.execute(
    "SELECT ticker, freq, start, end FROM kline_cache WHERE start > end"
).fetchall()
print(f"  异常段数: {len(bad_order)}")
for r in bad_order:
    data = conn.execute(
        "SELECT data FROM kline_cache WHERE ticker=? AND freq=? AND start=? AND end=?", r[:4]
    ).fetchone()[0]
    df = pickle.loads(data)
    ts = df["timestamps"]
    actual_s = str(ts.iloc[0])[:10]
    actual_e = str(ts.iloc[-1])[:10]
    days_covered = ts.iloc[-1] - ts.iloc[0]
    if hasattr(days_covered, "days"):
        days_covered = days_covered.days
    else:
        days_covered = abs(int(days_covered)) // 86400
    print(f"    [{r[1]}] {r[0]}: DB={r[2]}~{r[3]}, actual={actual_s}~{actual_e}, rows={len(df)}")
    if days_covered > 1000:
        print("    -> 数据实际完整，仅元数据日期记录有误")

# [5] 遗漏
print("\n[5] 遗漏检查 (同花顺股票池 vs 缓存)")
try:
    from trade_krono_cli.universe.provider import TongHuaShunUniverseProvider

    p = TongHuaShunUniverseProvider()
    tickets = p.get_universe()
    universe_codes = {t.ticker.split(".")[1] for t in tickets}
    cached_codes = {t[0] for t in conn.execute("SELECT DISTINCT SUBSTR(ticker,4) FROM kline_cache")}
    missing = sorted(universe_codes - cached_codes)
    print(f"  股票池: {len(universe_codes)}  已缓存: {len(cached_codes)}  遗漏: {len(missing)}")
    bj_miss = [c for c in missing if c.startswith("9")]
    sh_miss = [c for c in missing if c.startswith(("6", "5"))]
    sz_miss = [c for c in missing if not c.startswith(("6", "5", "9"))]
    print(f"  北交所遗漏: {len(bj_miss)}  ({bj_miss})")
    print(f"  上交所遗漏: {len(sh_miss)}  ({sh_miss})")
    print(f"  深交所遗漏: {len(sz_miss)}  ({sz_miss})")
except Exception as e:
    print(f"  无法获取股票池: {e}")

# [6] TTL
print("\n[6] 缓存 TTL 分布")
for ttl, cnt in conn.execute("SELECT ttl, COUNT(*) FROM kline_cache GROUP BY ttl").fetchall():
    label = "永久缓存" if ttl == 0.0 else f"TTL={ttl:.1f}s"
    print(f"  {label:>12}: {cnt:,}")

# [7] K线行数分布
print("\n[7] K线行数分布 (抽样200只)")
samples = conn.execute(
    "SELECT ticker, data FROM kline_cache WHERE freq='d' ORDER BY RANDOM() LIMIT 200"
).fetchall()
buckets: dict[str, int] = Counter()
for t, data in samples:
    df = pickle.loads(data)
    n = len(df)
    if n <= 10:
        buckets["<=10"] += 1
    elif n <= 50:
        buckets["11-50"] += 1
    elif n <= 200:
        buckets["51-200"] += 1
    elif n <= 500:
        buckets["101-500"] += 1
    else:
        buckets[">500"] += 1
for b in ["<=10", "11-50", "51-200", "101-500", ">500"]:
    pct = buckets[b] / 200 * 100
    print(f"  {b:>10}: {buckets[b]:>4} 只 ({pct:>5.1f}%)")

# [8] 多段股票
print("\n[8] 多段股票检查")
multi = conn.execute(
    "SELECT ticker, COUNT(*) as cnt FROM kline_cache WHERE freq='d' GROUP BY ticker HAVING cnt > 1 ORDER BY cnt DESC"
).fetchall()
print(f"  多段股票数: {len(multi)}")
for t, c in multi:
    rows = conn.execute(
        "SELECT start, end FROM kline_cache WHERE ticker=? AND freq='d' ORDER BY start", (t,)
    ).fetchall()
    total_k = sum(
        len(pickle.loads(r[0]))
        for r in conn.execute("SELECT data FROM kline_cache WHERE ticker=? AND freq='d'", (t,))
    )
    print(f"  {t}: {c}段, total_K_lines={total_k}")

# [9] NaN 检查
print("\n[9] NaN 检查 (抽样50只)")
nan_total = 0
sampled9 = conn.execute(
    "SELECT ticker, data FROM kline_cache WHERE freq='d' ORDER BY RANDOM() LIMIT 50"
).fetchall()
for t, data in sampled9:
    df = pickle.loads(data)
    nans = int(df.isna().sum().sum())
    nan_total += nans
print(f"  抽样NaN总数: {nan_total}")

# [10] DB 大小
db_size = os.path.getsize(DB)
print("\n[10] 数据库文件")
print(f"  文件大小: {db_size / 1024 / 1024:.1f} MB")

# 总结
sep = "=" * 55
print(f"\n{sep}")
issues = []
if corrupted > 0:
    issues.append(f"损坏段{corrupted}个")
if len(dupes) > 0:
    issues.append(f"重复段{len(dupes)}个")
if len(overlap) > 0:
    issues.append(f"重叠段{len(overlap)}个")
if len(bad_order) > 0:
    issues.append(f"日期倒序{len(bad_order)}个")
if len(missing) > 0:
    issues.append(f"遗漏{len(missing)}只")
if nan_total > 0:
    issues.append(f"NaN值{nan_total}个")
if not issues:
    print("  OK 全部检查通过，数据完整无损！")
else:
    print(f"  WARN 发现问题: {', '.join(issues)}")
print(f"{sep}")
