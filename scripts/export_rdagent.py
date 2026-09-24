#!/usr/bin/env python3
"""
将 trade-krono-cli 数据导出为 RD-Agent 格式

RD-Agent 格式要求:
  - Index: ['date', 'instrument']
  - Columns: $open, $close, $high, $low, $volume, $factor
  - 保存路径: /run/media/onai/MyDisk/Work/RD-Agent-Work/git_ignore_folder/factor_implementation_source_data/daily_pv_full.parquet
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pandas as pd
from loguru import logger

try:
    from trade_krono_cli.config import get_settings  # type: ignore[import-not-found]
except ImportError:
    get_settings = None  # type: ignore[assignment]

# ── 路径常量 ────────────────────────────────────────────────────────────────

WORK_DIR = Path("/run/media/onai/MyDisk/Work")
RD_AGENT_SOURCE = WORK_DIR / "RD-Agent-Work" / "git_ignore_folder" / "factor_implementation_source_data"
PARQUET_DEST = RD_AGENT_SOURCE / "daily_pv_full.parquet"


def _get_cache_db_path() -> Path:
    """获取 pipeline_cache.db 路径（兼容测试隔离）"""
    if get_settings is not None:
        try:
            s = get_settings()
            return Path(s.cache_dir) / "pipeline_cache.db"
        except Exception:
            pass
    return Path("outputs/cache/pipeline_cache.db")


def _ticker_to_instrument(ticker: str) -> str:
    """将 sh.600519 → SH600519（RD-Agent 格式）"""
    if "." in ticker:
        prefix, code = ticker.split(".", 1)
        return f"{prefix.upper()}{code}"
    return ticker.upper()


def export_to_rdagent(db_path: Path, dest: Path) -> dict:
    """从 SQLite 导出为 RD-Agent parquet 格式"""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    rows: list[pd.DataFrame] = []
    cur.execute("SELECT ticker, data FROM kline_cache")
    for ticker, blob in cur.fetchall():
        try:
            df = pd.read_pickle(pd.io.common.BytesIO(blob))
            df = df.rename(columns={"timestamps": "date"})
            df["date"] = pd.to_datetime(df["date"])
            df["instrument"] = _ticker_to_instrument(ticker)
            rows.append(df)
        except Exception as e:
            logger.warning(f"  跳过 {ticker}: {e}")

    conn.close()
    if not rows:
        logger.warning("  无数据可导出")
        return {"success": 0}

    # 合并所有 DataFrame
    panel = pd.concat(rows, ignore_index=True)
    panel = panel.dropna(subset=["open", "close", "volume"])
    panel = panel[panel["high"] > 0]  # 排除无效数据

    # 确保列存在并重命名（添加 $ 前缀，RD-Agent 规范）
    col_map = {
        "open": "$open",
        "high": "$high",
        "low": "$low",
        "close": "$close",
        "volume": "$volume",
    }
    panel = panel.rename(columns=col_map)
    panel["$factor"] = 1.0  # 前复权因子默认为 1.0

    # 先设置 MultiIndex，再选择列（保留 date/instrument 在 index 中）
    panel = panel.set_index(["date", "instrument"]).sort_index()
    panel = panel[~panel.index.duplicated(keep="last")]

    # 选择 RD-Agent 需要的列
    cols = ["$open", "$close", "$high", "$low", "$volume", "$factor"]
    panel = panel[cols]

    # 保存
    dest.parent.mkdir(parents=True, exist_ok=True)
    panel.to_parquet(dest, engine="pyarrow", compression="snappy")

    n_stocks = panel.index.get_level_values("instrument").nunique()
    n_rows = len(panel)
    logger.info(f"  ✅ RD-Agent Parquet 导出完成: {n_rows:,} 行, {n_stocks} 只股票")
    logger.info(f"     数据范围: {panel.index.get_level_values('date').min()} ~ {panel.index.get_level_values('date').max()}")
    logger.info(f"     保存路径: {dest}")

    return {"success": n_stocks, "rows": n_rows, "path": str(dest)}


def main() -> None:
    db_path = _get_cache_db_path()
    if not db_path.exists():
        logger.error(f"❌ 缓存数据库不存在: {db_path}")
        sys.exit(1)

    logger.info("🚀 开始导出 RD-Agent 格式")
    logger.info(f"   源数据库: {db_path}")
    logger.info(f"   目标路径: {PARQUET_DEST}")

    result = export_to_rdagent(db_path, PARQUET_DEST)

    if result.get("success", 0) > 0:
        logger.info(f"✅ 导出成功: {result['success']} 只股票")
    else:
        logger.error("❌ 导出失败")
        sys.exit(1)


if __name__ == "__main__":
    main()
