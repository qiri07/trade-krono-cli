#!/usr/bin/env python3
"""清理重复记录"""
from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path("outputs/cache/pipeline_cache.db")


def main() -> None:
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    print("=== 清理重复记录 ===")
    cur.execute("SELECT COUNT(*) FROM kline_cache")
    before = cur.fetchone()[0]
    print(f"清理前: {before} 条")

    # 删除data_len不是最大的记录
    cur.execute("""
        DELETE FROM kline_cache
        WHERE LENGTH(data) < (
            SELECT max_len FROM (
                SELECT ticker, MAX(LENGTH(data)) as max_len
                FROM kline_cache
                GROUP BY ticker
            ) t
            WHERE t.ticker = kline_cache.ticker
        )
    """)
    deleted = cur.rowcount
    conn.commit()
    print(f"删除了 {deleted} 条重复记录")

    cur.execute("SELECT COUNT(*) FROM kline_cache")
    after = cur.fetchone()[0]
    cur.execute("SELECT COUNT(DISTINCT ticker) FROM kline_cache")
    tickers = cur.fetchone()[0]
    print(f"清理后: {after} 条, {tickers} 只股票")

    # 检查是否还有重复
    cur.execute("SELECT ticker, COUNT(*) FROM kline_cache GROUP BY ticker HAVING COUNT(*) > 1")
    dupes = cur.fetchall()
    print(f"仍有重复: {len(dupes)} 只")

    # 检查2020年覆盖率
    cur.execute("""
        SELECT COUNT(DISTINCT ticker) FROM kline_cache
        WHERE start <= '2020-12-31' AND end >= '2020-01-01'
    """)
    has_2020 = cur.fetchone()[0]
    print(f"2020年数据: {has_2020} 只 ({has_2020*100/tickers:.1f}%)")

    conn.close()
    print("✅ 完成")


if __name__ == "__main__":
    main()
