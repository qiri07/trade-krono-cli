#!/usr/bin/env python3
"""
共享数据导出工具 — 将 trade-krono-cli 的 K 线缓存导出为多项目共用格式

支持格式:
  - CSV (per-stock): 供 Kronos、backtrader 使用
  - Parquet: 供 RD-Agent、qlib 使用
  - HDF5: 供 RD-Agent 遗留脚本使用
  - Qlib Binary: 供 qlib 使用

用法:
  uv run python scripts/export_shared_data.py --format csv --dest ~/Work/shared_data
  uv run python scripts/export_shared_data.py --format all --dest ~/Work/shared_data
"""

from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

import pandas as pd
from loguru import logger

try:
    from trade_krono_cli.config import get_settings  # type: ignore[import-not-found]
except ImportError:
    get_settings = None  # type: ignore[assignment]


# ── 路径常量 ────────────────────────────────────────────────────────────────

SHARED_DATA_ROOT = Path.home() / "Work" / "shared_data"

CSV_DIR = SHARED_DATA_ROOT / "astock_daily_csv"
QLIB_CSV_DIR = SHARED_DATA_ROOT / "astock_daily_qlib_csv"  # qlib 兼容格式（带 symbol 列）
TRADINGAGENTS_CSV_DIR = SHARED_DATA_ROOT / "astock_daily_ta"  # TradingAgents-astock 兼容格式
PARQUET_FILE = SHARED_DATA_ROOT / "astock_daily.parquet"
H5_FILE = SHARED_DATA_ROOT / "astock_daily.h5"
QLIB_DIR = SHARED_DATA_ROOT / "qlib_data"
META_FILE = SHARED_DATA_ROOT / "meta.json"


def _get_cache_db_path() -> Path:
    """获取 pipeline_cache.db 路径（兼容测试隔离）"""
    if get_settings is not None:
        try:
            s = get_settings()
            return Path(s.cache_dir) / "pipeline_cache.db"
        except Exception:
            pass
    # fallback: 默认路径
    return Path("outputs/cache/pipeline_cache.db")


def _ticker_to_symbol(ticker: str) -> str:
    """将 sh.600519 → SH600519（qlib/RD-Agent 格式）；无点号则直接返回大写"""
    if "." in ticker:
        prefix, code = ticker.split(".", 1)
        return f"{prefix.upper()}{code}"
    return ticker.upper()


def _symbol_to_ticker(symbol: str) -> str:
    """将 SH600519 → sh.600519"""
    if len(symbol) < 7:
        return symbol
    prefix = symbol[:2].lower()
    code = symbol[2:]
    return f"{prefix}.{code}"


# ── 导出：CSV（per-stock） ─────────────────────────────────────────────────


def export_csv(db_path: Path, dest: Path, progress_interval: int = 500) -> dict:
    """导出为 per-stock CSV 文件（Kronos / backtrader 格式）"""
    dest.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("SELECT DISTINCT ticker FROM kline_cache ORDER BY ticker")
    tickers = [r[0] for r in cur.fetchall()]

    success = 0
    failed: list[str] = []
    total_rows = 0

    for i, ticker in enumerate(tickers):
        cur.execute("SELECT data FROM kline_cache WHERE ticker = ?", (ticker,))
        row = cur.fetchone()
        if row is None:
            continue
        try:
            df = pd.read_pickle(pd.io.common.BytesIO(row[0]))
            # 标准化列名 & 日期格式
            df = df.rename(columns={"timestamps": "date"})
            df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
            cols = ["date", "open", "high", "low", "close", "volume", "amount"]
            df = df[[c for c in cols if c in df.columns]]
            safe_name = ticker.replace(".", "_")
            out_path = dest / f"{safe_name}.csv"
            df.to_csv(out_path, index=False)
            success += 1
            total_rows += len(df)
        except Exception as e:
            failed.append(f"{ticker}: {e}")
        if (i + 1) % progress_interval == 0:
            logger.info(f"  CSV 导出进度: {i + 1}/{len(tickers)}")

    conn.close()
    logger.info(f"  ✅ CSV 导出完成: {success} 只, 失败 {len(failed)} 只")
    return {"success": success, "failed": failed, "total_rows": total_rows}


# ── 导出：Parquet（panel） ─────────────────────────────────────────────────


def export_parquet(db_path: Path, dest: Path) -> dict:
    """导出为 MultiIndex Parquet（RD-Agent / qlib 格式）"""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    rows: list[dict] = []
    cur.execute("SELECT ticker, data FROM kline_cache")
    for ticker, blob in cur.fetchall():
        try:
            df = pd.read_pickle(pd.io.common.BytesIO(blob))
            df = df.rename(columns={"timestamps": "date"})
            df["date"] = pd.to_datetime(df["date"])
            df["ticker"] = _ticker_to_symbol(ticker)
            rows.append(df)
        except Exception as e:
            logger.warning(f"  Parquet 跳过 {ticker}: {e}")

    conn.close()
    if not rows:
        logger.warning("  无数据可导出")
        return {"success": 0}

    panel = pd.concat(rows, ignore_index=True)
    panel = panel.set_index(["date", "ticker"]).sort_index()
    # 移除多余的 amount 列如果不存在
    dest.parent.mkdir(parents=True, exist_ok=True)
    panel.to_parquet(dest, engine="pyarrow", compression="snappy")
    logger.info(
        f"  ✅ Parquet 导出完成: {panel.shape} ({dest.stat().st_size / 1024 / 1024:.1f} MB)"
    )
    return {"success": len(panel.index.get_level_values("ticker").unique()), "shape": panel.shape}


# ── 导出：HDF5（panel，RD-Agent 兼容） ────────────────────────────────────


def export_hdf5(db_path: Path, dest: Path) -> dict:
    """导出为 HDF5 Panel 格式（RD-Agent daily_pv.h5 兼容）"""
    try:
        import tables  # noqa: F401  # pytables 是 pandas to_hdf 的依赖
    except ImportError:
        logger.warning("  ⚠️  pytables 未安装，跳过 HDF5 导出（Parquet 已可用）")
        return {"success": 0, "skipped": "pytables not installed"}
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    rows: list[dict] = []
    cur.execute("SELECT ticker, data FROM kline_cache")
    for ticker, blob in cur.fetchall():
        try:
            df = pd.read_pickle(pd.io.common.BytesIO(blob))
            df = df.rename(columns={"timestamps": "date"})
            df["date"] = pd.to_datetime(df["date"])
            df["ticker"] = _ticker_to_symbol(ticker)
            rows.append(df)
        except Exception as e:
            logger.warning(f"  HDF5 跳过 {ticker}: {e}")

    conn.close()
    if not rows:
        return {"success": 0}

    panel = pd.concat(rows, ignore_index=True)
    panel = panel.set_index(["date", "ticker"]).sort_index()
    dest.parent.mkdir(parents=True, exist_ok=True)
    panel.to_hdf(dest, key="data", mode="w", format="table")
    logger.info(f"  ✅ HDF5 导出完成: {panel.shape} ({dest.stat().st_size / 1024 / 1024:.1f} MB)")
    return {"success": len(panel.index.get_level_values("ticker").unique()), "shape": panel.shape}


# ── 导出：TradingAgents-astock 兼容 CSV ─────────────────────────────────────


def export_tradingagents_csv(db_path: Path, dest: Path, progress_interval: int = 500) -> dict:
    """导出为 TradingAgents-astock 兼容的 CSV 格式（无 symbol 列，文件名为 {code}-astock-daily.csv）"""
    dest.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("SELECT DISTINCT ticker FROM kline_cache ORDER BY ticker")
    tickers = [r[0] for r in cur.fetchall()]

    success = 0
    failed: list[str] = []
    total_rows = 0

    for i, ticker in enumerate(tickers):
        cur.execute("SELECT data FROM kline_cache WHERE ticker = ?", (ticker,))
        row = cur.fetchone()
        if row is None:
            continue
        try:
            df = pd.read_pickle(pd.io.common.BytesIO(row[0]))
            df = df.rename(columns={"timestamps": "Date"})
            df["Date"] = pd.to_datetime(df["Date"]).dt.strftime("%Y-%m-%d")
            # 列名大写，与 TradingAgents 期望一致
            df = df.rename(
                columns={
                    "open": "Open",
                    "high": "High",
                    "low": "Low",
                    "close": "Close",
                    "volume": "Volume",
                }
            )
            cols = ["Date", "Open", "High", "Low", "Close", "Volume"]
            df = df[[c for c in cols if c in df.columns]]
            # 文件名：sh.600519 → 600519-astock-daily.csv
            code = ticker.split(".")[-1]
            out_path = dest / f"{code}-astock-daily.csv"
            df.to_csv(out_path, index=False)
            success += 1
            total_rows += len(df)
        except Exception as e:
            failed.append(f"{ticker}: {e}")
        if (i + 1) % progress_interval == 0:
            logger.info(f"  TA CSV 导出进度: {i + 1}/{len(tickers)}")

    conn.close()
    logger.info(f"  ✅ TradingAgents CSV 导出完成: {success} 只, 失败 {len(failed)} 只")
    return {"success": success, "failed": failed, "total_rows": total_rows}


# ── 导出：Qlib 兼容 CSV（带 symbol 列） ─────────────────────────────────────


def export_qlib_csv(db_path: Path, dest: Path, progress_interval: int = 500) -> dict:
    """导出为 qlib dump_bin 兼容的 CSV 格式（含 symbol 列，文件名用 SH600519 格式）"""
    dest.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("SELECT DISTINCT ticker FROM kline_cache ORDER BY ticker")
    tickers = [r[0] for r in cur.fetchall()]

    success = 0
    failed: list[str] = []
    total_rows = 0

    for i, ticker in enumerate(tickers):
        cur.execute("SELECT data FROM kline_cache WHERE ticker = ?", (ticker,))
        row = cur.fetchone()
        if row is None:
            continue
        try:
            df = pd.read_pickle(pd.io.common.BytesIO(row[0]))
            df = df.rename(columns={"timestamps": "date"})
            df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
            # 注意：不包含 symbol 列，qlib dump_bin 从文件名推断 symbol
            cols = ["date", "open", "high", "low", "close", "volume", "amount"]
            df = df[[c for c in cols if c in df.columns]]
            symbol = _ticker_to_symbol(ticker)
            out_path = dest / f"{symbol}.csv"
            df.to_csv(out_path, index=False)
            success += 1
            total_rows += len(df)
        except Exception as e:
            failed.append(f"{ticker}: {e}")
        if (i + 1) % progress_interval == 0:
            logger.info(f"  Qlib CSV 导出进度: {i + 1}/{len(tickers)}")

    conn.close()
    logger.info(f"  ✅ Qlib CSV 导出完成: {success} 只, 失败 {len(failed)} 只")
    return {"success": success, "failed": failed, "total_rows": total_rows}


# ── 导出：Qlib Binary（dump_bin） ──────────────────────────────────────────


def export_qlib(db_path: Path, dest: Path, qlib_dir_name: str = "shared") -> dict:
    """导出为 Qlib Binary 格式（CSV → dump_bin.py → .bin）"""
    import subprocess

    # 先生成 qlib 兼容 CSV
    csv_dest = dest / "csv_input"
    csv_result = export_qlib_csv(db_path, csv_dest, progress_interval=1000)

    # 生成 qlib 所需的 instruments 和 calendars 文件
    (dest / "calendars").mkdir(parents=True, exist_ok=True)
    (dest / "features").mkdir(parents=True, exist_ok=True)
    (dest / "instruments").mkdir(parents=True, exist_ok=True)

    # calendars: 从数据中提取交易日
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT MIN(start) FROM kline_cache")
    start_date = cur.fetchone()[0]
    cur.execute("SELECT MAX(end) FROM kline_cache")
    end_date = cur.fetchone()[0]
    conn.close()

    cal_df = pd.DataFrame({"datetime": pd.date_range(start_date, end_date, freq="D")})
    cal_df = cal_df[~cal_df["datetime"].dt.dayofweek.isin([5, 6])]
    cal_df["datetime"] = cal_df["datetime"].dt.strftime("%Y-%m-%d")
    cal_df.to_csv(dest / "calendars" / "day.txt", index=False, header=False)

    # instruments: 所有 ticker
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT ticker FROM kline_cache ORDER BY ticker")
    tickers = [r[0] for r in cur.fetchall()]
    conn.close()

    inst_lines = []
    for t in tickers:
        symbol = _ticker_to_symbol(t)
        inst_lines.append(f"{symbol}\t{start_date}\t{end_date}")
    (dest / "instruments" / "all.txt").write_text("\n".join(inst_lines))

    try:
        qlib_root = Path(__file__).resolve().parents[1].parent / "qlib"
        dump_bin_script = qlib_root / "scripts" / "dump_bin.py"
        if dump_bin_script.exists():
            logger.info(f"  运行 dump_bin.py → {dest} ...")
            result = subprocess.run(
                [
                    "python",
                    str(dump_bin_script),
                    "dump_all",
                    "--data_path",
                    str(csv_dest),
                    "--qlib_dir",
                    str(dest),
                    "--freq",
                    "day",
                ],
                capture_output=True,
                text=True,
                timeout=600,
            )
            if result.returncode == 0:
                bin_count = len(list((dest / "features").glob("*")))
                logger.info(f"  ✅ Qlib binary 导出完成: {bin_count} 个特征文件")
                csv_result["bin_count"] = bin_count
            else:
                logger.warning(f"  ⚠️ dump_bin.py 失败: {result.stderr[:300]}")
                csv_result["bin_count"] = 0
        else:
            logger.warning(f"  ⚠️ dump_bin.py 不存在: {dump_bin_script}，仅生成 CSV 输入")
    except Exception as e:
        logger.warning(f"  ⚠️ dump_bin.py 执行异常: {e}")
        csv_result["bin_count"] = 0

    return csv_result


# ── 元数据写入 ─────────────────────────────────────────────────────────────


def _write_meta(dest: Path, results: dict) -> None:
    import json

    meta: dict = {
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "results": results,
    }
    (dest / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))


# ── CLI ────────────────────────────────────────────────────────────────────


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="导出 trade-krono-cli 数据为共享格式")
    parser.add_argument(
        "--format",
        choices=["csv", "parquet", "hdf5", "qlib_csv", "qlib", "ta_csv", "all"],
        default="all",
    )
    parser.add_argument("--dest", type=Path, default=SHARED_DATA_ROOT)
    parser.add_argument("--db", type=Path, default=None)
    args = parser.parse_args()

    db_path = Path(args.db) if args.db else _get_cache_db_path()
    if not db_path.exists():
        logger.error(f"❌ 缓存数据库不存在: {db_path}")
        sys.exit(1)

    logger.info(f"🚀 开始导出共享数据 → {args.dest}")
    logger.info(f"   源数据库: {db_path}")

    results: dict = {}

    fmt = args.format
    if fmt in ("csv", "all"):
        results["csv"] = export_csv(db_path, args.dest / "astock_daily_csv")
    if fmt in ("parquet", "all"):
        results["parquet"] = export_parquet(db_path, args.dest / "astock_daily.parquet")
    if fmt in ("hdf5", "all"):
        results["hdf5"] = export_hdf5(db_path, args.dest / "astock_daily.h5")
    if fmt in ("qlib_csv", "qlib", "all"):
        results["qlib_csv"] = export_qlib_csv(db_path, args.dest / "astock_daily_qlib_csv")
    if fmt in ("ta_csv", "all"):
        results["ta_csv"] = export_tradingagents_csv(db_path, args.dest / "astock_daily_ta")
    if fmt in ("qlib", "all"):
        results["qlib"] = export_qlib(db_path, args.dest / "qlib_data")

    _write_meta(args.dest, results)
    logger.info("✅ 导出完成")


if __name__ == "__main__":
    main()
