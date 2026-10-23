"""自动发现并合并 K 线缓存中同一 ticker 的多条记录，消除重复。

背景：fill_missing_and_today.py 对部分股票直接 INSERT 了一条单行增量记录，
而不是合并进已有全量记录，导致同一 ticker 出现多条记录（无日期重叠）。

本脚本自动扫描 kline_cache 中所有出现 >1 条记录的 ticker，把多条记录合并为
一条完整记录（取并集日期，后写入的行数据优先），写回 DB，删除原 N 条，
INSERT 1 条。合并前自动备份 DB。
"""

from __future__ import annotations

import hashlib
import pickle
import shutil
import sqlite3
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

DB_PATH = Path("outputs/cache/pipeline_cache.db")
BACKUP_DIR = Path("outputs/cache/backups")


def discover_duplicates(cur: sqlite3.Cursor) -> list[str]:
    """自动发现 kline_cache 中出现 >1 条记录的所有 ticker。"""
    return [
        r[0]
        for r in cur.execute(
            "SELECT ticker FROM kline_cache GROUP BY ticker HAVING COUNT(*) > 1 ORDER BY ticker"
        )
    ]


def _backup_db(conn: sqlite3.Connection) -> Path:
    """合并前把 DB 备份到 backups 目录，返回备份路径。"""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / f"pipeline_cache.pre_merge_{ts}.db"
    conn.commit()
    shutil.copy2(DB_PATH, backup_path)
    print(f"备份已创建: {backup_path}")
    return backup_path


def _compute_data_hash(data: bytes) -> str:
    """使用与缓存层一致的 SHA-256 with b'TKC1' 前缀计算数据哈希。"""
    hasher = hashlib.sha256()
    hasher.update(b"TKC1")
    hasher.update(data)
    return hasher.hexdigest()


def main() -> None:
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    duplicates = discover_duplicates(cur)
    print(f"发现重复 ticker: {len(duplicates)} 只")
    if not duplicates:
        print("无重复，无需合并。")
        conn.close()
        return

    _backup_db(conn)

    for ticker in duplicates:
        rows = cur.execute(
            "SELECT rowid, start, end, freq, ttl, data, created, adjustflag, "
            "data_hash, source_provider, source_version FROM kline_cache WHERE ticker=?",
            (ticker,),
        ).fetchall()

        if len(rows) < 2:
            print(f"{ticker}: 仅 {len(rows)} 条记录，跳过")
            continue

        frames = []
        for rowid, start, end, _freq, _ttl, data, _created, _adjustflag, *_ in rows:
            df = pickle.loads(data)
            frames.append(df)
            print(f"{ticker}: rowid={rowid} {start}~{end} {len(df)}条")

        # 取第一列作为日期列
        date_col = frames[0].columns[0]
        merged = pd.concat(frames, ignore_index=True)
        merged[date_col] = merged[date_col].astype(str)
        merged = merged.sort_values(date_col).drop_duplicates(subset=[date_col], keep="last")
        merged = merged.reset_index(drop=True)

        new_start = str(merged[date_col].min())
        new_end = str(merged[date_col].max())
        new_blob = pickle.dumps(merged)

        # 使用第一条记录的元数据
        freq = rows[0][3]
        adjustflag = rows[0][7]
        source_provider = rows[0][9]
        source_version = rows[0][10]
        new_hash = _compute_data_hash(new_blob)

        # 删除旧记录，插入新记录
        for rowid, *_ in rows:
            cur.execute("DELETE FROM kline_cache WHERE rowid=?", (rowid,))
        cur.execute(
            "INSERT INTO kline_cache "
            "(ticker, start, end, freq, ttl, data, created, adjustflag, "
            "data_hash, source_provider, source_version) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                ticker,
                new_start,
                new_end,
                freq,
                0.0,
                new_blob,
                time.time(),
                adjustflag,
                new_hash,
                source_provider,
                source_version,
            ),
        )
        print(f"{ticker}: 合并 {len(rows)}条 → 1条 ({new_start}~{new_end}, {len(merged)}行)")

    conn.commit()

    # 验证
    dup = cur.execute(
        "SELECT ticker, COUNT(*) c FROM kline_cache GROUP BY ticker HAVING c>1"
    ).fetchall()
    print(f"\n仍有重复: {len(dup)} 只")
    total = cur.execute("SELECT COUNT(*) FROM kline_cache").fetchone()[0]
    tickers_n = cur.execute("SELECT COUNT(DISTINCT ticker) FROM kline_cache").fetchone()[0]
    print(f"总记录: {total}, 不重复 ticker: {tickers_n}, 差值: {total - tickers_n}")

    conn.close()
    print("✅ 完成")


if __name__ == "__main__":
    main()
