"""从主 pipeline_cache.db 同步增量数据到 outputs/cache/pipeline_cache.db，并导出 RD-Agent daily_pv。

用法：
  uv run python tests/sync_and_export.py
  uv run python tests/sync_and_export.py --dry-run   # 仅预览，不写入
  uv run python tests/sync_and_export.py --no-h5     # 跳过 h5 生成
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import time
from pathlib import Path

DB_MAIN = Path("pipeline_cache.db")
DB_OLD = Path("outputs/cache/pipeline_cache.db")
BACKUP_SUFFIX = "_pre_sync_" + str(int(time.time())) + ".bak"

_SYNCABLE_TABLES: tuple[str, ...] = (
    "kline_cache",
    "jobs",
    "ta_analysis",
    "kronos_forecast",
    "signals",
    "decisions",
    "ta_cache",
    "kronos_cache",
    "committee_deliberations",
    "raw_reports",
)


def _row_count(conn: sqlite3.Connection, table: str) -> int:
    try:
        return conn.execute("SELECT COUNT(*) FROM [" + table + "]").fetchone()[0]
    except sqlite3.OperationalError:
        return 0


def _get_pk_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    """返回表的主键列名列表。"""
    info = conn.execute("PRAGMA table_info([" + table + "])").fetchall()
    pk_set: set[str] = set()
    for col in info:
        cid, name, _, _, notnull, pk = col
        if pk:
            pk_set.add(name)
    return [c[1] for c in info if c[1] in pk_set]


def _table_cols(conn: sqlite3.Connection, table: str) -> list[str]:
    return [c[1] for c in conn.execute("PRAGMA table_info([" + table + "])").fetchall()]


def sync_incremental(dry_run: bool = False) -> dict[str, int]:
    """将主库的增量数据同步到旧库。

    Returns:
        各表同步的行数统计 {table: synced_count}
    """
    conn_main = sqlite3.connect(str(DB_MAIN))
    conn_old = sqlite3.connect(str(DB_OLD))

    stats: dict[str, int] = {}

    for table in _SYNCABLE_TABLES:
        main_cnt = _row_count(conn_main, table)
        old_cnt = _row_count(conn_old, table)
        if main_cnt == old_cnt:
            stats[table] = 0
            continue

        pk_cols = _get_pk_columns(conn_main, table)
        all_cols = _table_cols(conn_main, table)

        if not pk_cols:
            # 无主键表：全量 INSERT OR IGNORE
            cols_sql = ", ".join("[" + c + "]" for c in all_cols)
            try:
                n = conn_old.execute(
                    "INSERT OR IGNORE INTO ["
                    + table
                    + "] ("
                    + cols_sql
                    + ") SELECT "
                    + cols_sql
                    + " FROM ["
                    + table
                    + "]"
                ).rowcount
                stats[table] = n
            except Exception as e:
                print("  ⚠️  " + table + " 同步失败: " + str(e))
                stats[table] = 0
            continue

        # 主键差集：用 Python 集合比较，避免跨库 SQL 的复杂性
        all_cols_sql = ", ".join("[" + c + "]" for c in all_cols)
        pk_cols_sql = ", ".join("[" + c + "]" for c in pk_cols)

        old_keys: set[tuple] = set(
            conn_old.execute("SELECT " + pk_cols_sql + " FROM [" + table + "]").fetchall()
        )
        main_rows = conn_main.execute("SELECT " + all_cols_sql + " FROM [" + table + "]").fetchall()
        delta_rows = [r for r in main_rows if tuple(r[: len(pk_cols)]) not in old_keys]

        if not delta_rows:
            stats[table] = 0
            continue

        if not dry_run:
            placeholders = ", ".join(["?"] * len(all_cols))
            conn_old.executemany(
                "INSERT OR IGNORE INTO ["
                + table
                + "] ("
                + all_cols_sql
                + ") VALUES ("
                + placeholders
                + ")",
                delta_rows,
            )
            conn_old.commit()

        stats[table] = len(delta_rows)
        prefix = "[DRY-RUN] " if dry_run else ""
        print(
            "  "
            + prefix
            + table
            + ": "
            + format(old_cnt, ",")
            + " → "
            + format(old_cnt + len(delta_rows), ",")
            + " (+"
            + format(len(delta_rows), ",")
            + ")"
        )

    conn_main.close()
    conn_old.close()
    return stats


def export_rdagent(no_h5: bool = False) -> dict:
    """直接从旧库导出 RD-Agent daily_pv 格式（绕过 Cache 单例的隔离检查）。"""
    from io import BytesIO

    conn = sqlite3.connect(str(DB_OLD))
    rows_raw: list = []
    total = conn.execute("SELECT COUNT(*) FROM kline_cache").fetchone()[0]

    cursor = conn.execute("SELECT ticker, data FROM kline_cache")
    import pandas as pd

    for i, (ticker, blob) in enumerate(cursor, 1):
        df = pd.read_pickle(BytesIO(blob))
        df["instrument"] = ticker.replace(".", "").upper()
        rows_raw.append(df)
        if i % 1000 == 0:
            print(f"  读取缓存 {i}/{total} 只...")

    conn.close()

    combined = pd.concat(rows_raw, ignore_index=True)
    print(f"导出原始: {len(combined):,} 行, {combined['instrument'].nunique()} 只")

    df = combined.copy()
    df["date"] = pd.to_datetime(df["timestamps"]).dt.normalize()
    df = df.rename(
        columns={
            "open": "$open",
            "high": "$high",
            "low": "$low",
            "close": "$close",
            "volume": "$volume",
        }
    )
    df["$factor"] = 1.0
    df = df.dropna(subset=["$open", "$close", "$volume"])
    df = df[df["$high"] > 0]
    df = df.set_index(["date", "instrument"]).sort_index()
    df.index.names = ["date", "instrument"]
    df = df[["$open", "$close", "$high", "$low", "$volume", "$factor"]]

    stocks = int(df.index.get_level_values("instrument").nunique())
    date_min = df.index.get_level_values("date").min().strftime("%Y-%m-%d")
    date_max = df.index.get_level_values("date").max().strftime("%Y-%m-%d")

    base_dir = Path("RD-Agent-Work") / "git_ignore_folder" / "factor_implementation_source_data"
    parquet_path = base_dir / "daily_pv.parquet"
    base_dir.mkdir(parents=True, exist_ok=True)
    df.to_parquet(str(parquet_path), engine="pyarrow")
    size_mb = Path(parquet_path).stat().st_size / 1024 / 1024
    print(f"✅ parquet: {parquet_path} ({size_mb:.1f} MB)")

    result: dict = {
        "rows": len(df),
        "stocks": stocks,
        "date_min": date_min,
        "date_max": date_max,
        "parquet_path": str(parquet_path),
    }

    # h5 生成
    if not no_h5:
        h5_path = base_dir / "daily_pv.h5"
        try:
            import os as _os
            import subprocess

            _base = Path(__file__).resolve().parents[2]
            env_py = _base / "RD-Agent-Work" / "rdagent-env" / "bin" / "python"
            if not env_py.exists():
                env_py = _base / "rdagent-env" / "bin" / "python"
            if env_py.exists():
                r = subprocess.run(
                    [
                        str(env_py),
                        "-c",
                        "import os, pandas as pd; "
                        "df=pd.read_parquet(os.environ['PARQUET']); "
                        "df.to_hdf(os.environ['H5'], key='data', mode='w')",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=180,
                    env={**_os.environ, "PARQUET": str(parquet_path), "H5": str(h5_path)},
                )
                if r.returncode == 0:
                    h5_size = Path(h5_path).stat().st_size / 1024 / 1024
                    print(f"✅ h5: {h5_path} ({h5_size:.1f} MB)")
                    result["h5_path"] = str(h5_path)
                    result["h5_size_mb"] = round(h5_size, 1)
                else:
                    print(f"⚠️  h5 生成失败: {r.stderr.strip()}")
            else:
                print("⚠️  未找到 rdagent-env/python，跳过 h5")
        except Exception as e:
            print(f"⚠️  h5 生成异常: {e}")

    # debug 数据集
    debug_insts = 100
    debug_dir = base_dir.parent / (base_dir.name + "_debug")
    debug_path = debug_dir / "daily_pv.parquet"
    insts = df.index.get_level_values("instrument").unique()[:debug_insts]
    debug_df = df.loc[pd.IndexSlice[:, insts], :]
    debug_dir.mkdir(parents=True, exist_ok=True)
    debug_df.to_parquet(str(debug_path), engine="pyarrow")
    result["debug_path"] = str(debug_path)
    result["debug_rows"] = len(debug_df)
    result["debug_stocks"] = len(insts)
    print(
        f"✅ debug: {debug_path} "
        f"({len(insts)} 只, {debug_df.index.get_level_values('date').min()} ~ "
        f"{debug_df.index.get_level_values('date').max()})"
    )

    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="同步 pipeline_cache.db 增量数据并导出 RD-Agent 格式"
    )
    parser.add_argument("--dry-run", action="store_true", help="仅预览，不写入旧库")
    parser.add_argument("--no-h5", action="store_true", help="跳过 h5 生成")
    args = parser.parse_args()

    sep = "=" * 60
    print(sep)
    print("Pipeline Cache 增量同步 + RD-Agent 导出")
    print(sep)

    if not args.dry_run:
        backup_path = DB_OLD.parent / (DB_OLD.name + BACKUP_SUFFIX)
        print("\n📦 备份旧库 → " + str(backup_path))
        shutil.copy2(DB_OLD, backup_path)
        print("  ✅ 备份完成")

    print("\n🔄 同步增量数据 (dry_run=" + str(args.dry_run) + ") ...")
    stats = sync_incremental(dry_run=args.dry_run)

    total_synced = sum(stats.values())
    print("\n✅ 同步完成: 共 " + format(total_synced, ",") + " 行增量数据")
    for table, n in stats.items():
        if n:
            print("   " + table + ": +" + format(n, ","))

    print("\n📊 旧库当前状态:")
    conn = sqlite3.connect(str(DB_OLD))
    for table in _SYNCABLE_TABLES:
        cnt = _row_count(conn, table)
        if cnt:
            print("   " + table + ": " + format(cnt, ","))
    conn.close()

    print("\n📤 导出 RD-Agent daily_pv (no_h5=" + str(args.no_h5) + ") ...")
    export_stats = export_rdagent(no_h5=args.no_h5)
    print("   股票数 : " + format(export_stats["stocks"], ","))
    print("   数据行 : " + format(export_stats["rows"], ","))
    print("   日期范围: " + export_stats["date_min"] + " ~ " + export_stats["date_max"])
    print("   parquet: " + export_stats["parquet_path"])
    if export_stats.get("h5_path"):
        print(
            "   h5     : "
            + export_stats["h5_path"]
            + " ("
            + str(export_stats.get("h5_size_mb", "?"))
            + " MB)"
        )
    if export_stats.get("debug_path"):
        print(
            "   debug  : "
            + export_stats["debug_path"]
            + " ("
            + format(export_stats.get("debug_rows", 0), ",")
            + " 行)"
        )

    print("\n" + sep)
    print("全部完成 ✅")
    print(sep)


if __name__ == "__main__":
    main()
