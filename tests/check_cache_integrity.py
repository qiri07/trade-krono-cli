#!/usr/bin/env python3
"""K线缓存完整性检查."""

from __future__ import annotations

import os
import pickle
import sqlite3
from collections import Counter

DB = "outputs/cache/pipeline_cache.db"
conn = sqlite3.connect(DB)


# [1] 基础统计
total_rows = conn.execute("SELECT COUNT(*) FROM kline_cache").fetchone()[0]
unique_tickers = conn.execute("SELECT COUNT(DISTINCT ticker) FROM kline_cache").fetchone()[0]
unique_tf = conn.execute(
    "SELECT COUNT(*) FROM (SELECT DISTINCT ticker, freq FROM kline_cache)",
).fetchone()[0]
dupes = conn.execute(
    "SELECT ticker, freq, start, end, COUNT(*) as cnt FROM kline_cache "
    "GROUP BY ticker, freq, start, end HAVING cnt > 1",
).fetchall()
sh = conn.execute(
    "SELECT COUNT(DISTINCT ticker) FROM kline_cache WHERE ticker LIKE 'sh.%'",
).fetchone()[0]
sz = conn.execute(
    "SELECT COUNT(DISTINCT ticker) FROM kline_cache WHERE ticker LIKE 'sz.%'",
).fetchone()[0]
bj = conn.execute(
    "SELECT COUNT(DISTINCT ticker) FROM kline_cache WHERE ticker LIKE 'bj.%'",
).fetchone()[0]


# [2] 损坏检查
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

# [3] 重叠段
overlap = conn.execute("""
    SELECT a.ticker, a.freq, a.start, a.end, b.start, b.end
    FROM kline_cache a JOIN kline_cache b
      ON a.ticker=b.ticker AND a.freq=b.freq AND a.rowid < b.rowid
    WHERE a.end >= b.start AND a.start <= b.end
""").fetchall()
for _o in overlap[:5]:
    pass

# [4] start > end
bad_order = conn.execute(
    "SELECT ticker, freq, start, end FROM kline_cache WHERE start > end",
).fetchall()
for r in bad_order:
    data = conn.execute(
        "SELECT data FROM kline_cache WHERE ticker=? AND freq=? AND start=? AND end=?", r[:4],
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
    if days_covered > 1000:
        pass

# [5] 遗漏
try:
    from trade_krono_cli.universe.provider import TongHuaShunUniverseProvider

    p = TongHuaShunUniverseProvider()
    tickets = p.get_universe()
    universe_codes = {t.ticker.split(".")[1] for t in tickets}
    cached_codes = {t[0] for t in conn.execute("SELECT DISTINCT SUBSTR(ticker,4) FROM kline_cache")}
    missing = sorted(universe_codes - cached_codes)
    bj_miss = [c for c in missing if c.startswith("9")]
    sh_miss = [c for c in missing if c.startswith(("6", "5"))]
    sz_miss = [c for c in missing if not c.startswith(("6", "5", "9"))]
except Exception:
    pass

# [6] TTL
for ttl, _cnt in conn.execute("SELECT ttl, COUNT(*) FROM kline_cache GROUP BY ttl").fetchall():
    label = "永久缓存" if ttl == 0.0 else f"TTL={ttl:.1f}s"

# [7] K线行数分布
samples = conn.execute(
    "SELECT ticker, data FROM kline_cache WHERE freq='d' ORDER BY RANDOM() LIMIT 200",
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

# [8] 多段股票
multi = conn.execute(
    "SELECT ticker, COUNT(*) as cnt FROM kline_cache WHERE freq='d' GROUP BY ticker HAVING cnt > 1 ORDER BY cnt DESC",
).fetchall()
for t, _c in multi:
    rows = conn.execute(
        "SELECT start, end FROM kline_cache WHERE ticker=? AND freq='d' ORDER BY start", (t,),
    ).fetchall()
    total_k = sum(
        len(pickle.loads(r[0]))
        for r in conn.execute("SELECT data FROM kline_cache WHERE ticker=? AND freq='d'", (t,))
    )

# [9] NaN 检查
nan_total = 0
sampled9 = conn.execute(
    "SELECT ticker, data FROM kline_cache WHERE freq='d' ORDER BY RANDOM() LIMIT 50",
).fetchall()
for t, data in sampled9:
    df = pickle.loads(data)
    nans = int(df.isna().sum().sum())
    nan_total += nans

# [10] DB 大小
db_size = os.path.getsize(DB)

# 总结
sep = "=" * 55
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
    pass
else:
    pass
