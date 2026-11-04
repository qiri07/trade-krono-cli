#!/usr/bin/env python3
"""
DB → parquet 转换脚本（必须在 trade-krono-cli venv 运行，pandas 3.x 兼容 pickle）

用法：python scripts/czsc_convert_db.py
"""

from __future__ import annotations

import pickle
import sqlite3
from pathlib import Path

import pandas as pd
from loguru import logger

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "outputs/cache/pipeline_cache.db"
BARS_DIR = PROJECT_ROOT / "outputs/czsc_bars"
VALID_PREFIXES = ("sh.", "sz.", "bj.")
MIN_BARS = 60


def main() -> int:
    BARS_DIR.mkdir(parents=True, exist_ok=True)

    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    cur = con.cursor()
    rows = cur.execute(
        "select ticker, data from kline_cache where freq='d' and (ticker like ? or ticker like ? or ticker like ?)",
        (f"{VALID_PREFIXES[0]}%", f"{VALID_PREFIXES[1]}%", f"{VALID_PREFIXES[2]}%"),
    ).fetchall()
    con.close()

    print(f"[db] freq=d 记录 {len(rows)} 条", flush=True)

    converted = skipped = 0
    for ticker, blob in rows:
        out_path = BARS_DIR / f"{ticker}.parquet"
        # 增量检查：若 parquet 已存在，仅在 DB end > parquet 最大日期时重新导出
        try:
            df_existing = pd.read_parquet(out_path, columns=["dt"])
            if len(df_existing) > 0:
                parquet_max = pd.to_datetime(df_existing["dt"]).max()
                df_blob = pickle.loads(blob)
                ts = df_blob["timestamps"]
                if ts.dtype == object or str(ts.dtype).startswith("str"):
                    ts = pd.to_datetime(ts, format="mixed")
                db_end = ts.max()
                if pd.Timestamp(db_end) <= parquet_max:
                    skipped += 1
                    continue
        except Exception as e:
            logger.warning(f"⚠️  跳过 {ticker}: 读取缓存失败 - {e}")
            pass

        df = df_blob if "df_blob" in locals() else pickle.loads(blob)
        if len(df) < MIN_BARS:
            skipped += 1
            continue

        ts = df["timestamps"]
        if ts.dtype == object or str(ts.dtype).startswith("str"):
            ts = pd.to_datetime(ts, format="mixed")

        out = pd.DataFrame(
            {
                "dt": pd.to_datetime(ts).astype("datetime64[ns]"),
                "symbol": ticker,
                "open": df["open"].astype("float64"),
                "close": df["close"].astype("float64"),
                "high": df["high"].astype("float64"),
                "low": df["low"].astype("float64"),
                "vol": df["volume"].astype("float64"),
                "amount": df["amount"].astype("float64"),
            }
        )
        out = out.dropna(subset=["open", "close", "high", "low", "vol", "amount"])
        out = out.sort_values("dt", kind="stable").drop_duplicates("dt", keep="last").reset_index(drop=True)
        if out.empty:
            skipped += 1
            continue

        out.to_parquet(out_path, index=False)
        converted += 1

        if converted % 1000 == 0:
            print(f"  ... 已转换 {converted} 只", flush=True)

    print(f"Converted: {converted}, Skipped: {skipped}", flush=True)
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())

