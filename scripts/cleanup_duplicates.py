#!/usr/bin/env python3
"""清理重复记录"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts._utils import get_cache_db_path
from scripts.backup_cache import backup_all


def main() -> None:
    # ── 第零步：数据安全备份（在任何删除操作前自动备份）─────────────────
    backup_all(keep=10)

    db_path = get_cache_db_path()
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    print("=== 清理重复记录 ===")
    cur.execute("SELECT COUNT(*) FROM kline_cache")
    before = cur.fetchone()[0]
    print(f"清理前: {before} 条")

    # 对每个ticker，保留data最大的记录（完整历史优先），确保不丢失历史数据
    cur.execute("""
        DELETE FROM kline_cache
        WHERE rowid NOT IN (
            SELECT rowid FROM (
                SELECT rowid,
                       ROW_NUMBER() OVER (
                           PARTITION BY ticker
                           ORDER BY LENGTH(data) DESC
                       ) as rn
                FROM kline_cache
            )
            WHERE rn = 1
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
    print(f"2020年数据: {has_2020} 只 ({has_2020 * 100 / tickers:.1f}%)")

    conn.close()
    print("✅ 完成")


if __name__ == "__main__":
    main()
