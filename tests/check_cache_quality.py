#!/usr/bin/env python3
"""K线缓存质量深度检查"""

from __future__ import annotations

import pickle
import sqlite3

conn = sqlite3.connect("outputs/cache/pipeline_cache.db")

print("=== 进一步数据质量检查 ===")

# 1. 日期连续性
print("\n[日期连续性检查 - 抽样50只]")
gap_count = 0
sampled = conn.execute(
    "SELECT ticker, data FROM kline_cache WHERE freq='d' ORDER BY RANDOM() LIMIT 50"
).fetchall()
for t, data in sampled:
    df = pickle.loads(data)
    if len(df) < 2:
        continue
    timestamps = df["timestamps"]
    for i in range(1, len(timestamps)):
        diff = timestamps[i] - timestamps[i - 1]
        if hasattr(diff, "days"):
            diff_days = diff.days
        else:
            diff_days = abs(int(diff)) // 86400
        if diff_days > 3:
            gap_count += 1
            if gap_count <= 5:
                print(
                    f"  {t}: gap {diff_days}d between {str(timestamps.iloc[i - 1])[:10]} and {str(timestamps.iloc[i])[:10]}"
                )
print(f"  日期跳变总数: {gap_count}")

# 2. 价格合理性
print("\n[价格合理性检查 - 抽样50只]")
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
            print(f"  {t}: high<low={bad_hl}, high<close={bad_hc}, low>close={bad_lc}")
print(f"  价格异常股票: {price_issues}")

# 3. 成交量非负
print("\n[成交量/金额非负检查 - 抽样50只]")
vol_issues = 0
for t, data in sampled:
    df = pickle.loads(data)
    if len(df) < 2:
        continue
    bad_vol = int((df["volume"] < 0).sum() + (df["amount"] < 0).sum())
    if bad_vol > 0:
        vol_issues += 1
        if vol_issues <= 3:
            print(f"  {t}: negative count={bad_vol}")
print(f"  负值异常股票: {vol_issues}")

# 4. start>end 详细
print("\n[start>end 异常段详情]")
bad_order = conn.execute(
    "SELECT ticker, freq, start, end FROM kline_cache WHERE start > end"
).fetchall()
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
    print(
        f"  {r[0]} [{r[1]}]: DB={r[2]}~{r[3]}, actual={actual_s}~{actual_e}, rows={len(df)}, span={days_covered}d"
    )
    if days_covered > 1000:
        print("    -> 数据实际完整，仅元数据日期记录有误")

# 5. 多段股票的重复日期
print("\n[多段股票重复日期检查]")
multi = conn.execute(
    "SELECT ticker FROM kline_cache WHERE freq='d' GROUP BY ticker HAVING COUNT(*)>1"
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
        print(f"  {t}: {dupes} duplicate trading days across {len(rows)} segments")
print(f"  总重复交易日: {dup_dates}")

print("\n=== 检查完成 ===")
