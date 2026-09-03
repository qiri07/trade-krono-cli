#!/usr/bin/env python3
"""K线缓存质量深度检查."""

from __future__ import annotations

import pickle
import sqlite3

conn = sqlite3.connect("outputs/cache/pipeline_cache.db")


# 1. 日期连续性
gap_count = 0
sampled = conn.execute(
    "SELECT ticker, data FROM kline_cache WHERE freq='d' ORDER BY RANDOM() LIMIT 50",
).fetchall()
for t, data in sampled:
    df = pickle.loads(data)
    if len(df) < 2:
        continue
    timestamps = df["timestamps"]
    for i in range(1, len(timestamps)):
        diff = timestamps[i] - timestamps[i - 1]
        diff_days = diff.days if hasattr(diff, "days") else abs(int(diff)) // 86400
        if diff_days > 3:
            gap_count += 1
            if gap_count <= 5:
                pass

# 2. 价格合理性
price_issues = 0
for t, data in sampled:
    df = pickle.loads(data)
    if len(df) < 2:
        continue
    bad_hl = int((df["high"] < df["low"]).sum())
    bad_hc = int((df["high"] < df["close"]).sum())
    bad_lc = int((df["low"] > df["close"]).sum())
    if bad_hl + bad_hc + bad_lc > 0:
        price_issues += 1
        if price_issues <= 3:
            pass

# 3. 成交量非负
vol_issues = 0
for t, data in sampled:
    df = pickle.loads(data)
    if len(df) < 2:
        continue
    bad_vol = int((df["volume"] < 0).sum() + (df["amount"] < 0).sum())
    if bad_vol > 0:
        vol_issues += 1
        if vol_issues <= 3:
            pass

# 4. start>end 详细
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

# 5. 多段股票的重复日期
multi = conn.execute(
    "SELECT ticker FROM kline_cache WHERE freq='d' GROUP BY ticker HAVING COUNT(*)>1",
).fetchall()
dup_dates = 0
for (t,) in multi:
    rows = conn.execute("SELECT data FROM kline_cache WHERE ticker=? AND freq='d'", (t,)).fetchall()
    all_dates = []
    for (data,) in rows:
        df = pickle.loads(data)
        all_dates.extend(df["timestamps"].dt.strftime("%Y-%m-%d").tolist())
    dupes = len(all_dates) - len(set(all_dates))
    dup_dates += dupes
    if dupes > 0:
        pass

