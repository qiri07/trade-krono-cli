"""
Export shared data to vnpy alpha parquet format.
Converts CSV files from shared_data to vnpy's expected parquet format.

vnpy format per file ({vt_symbol}.parquet):
  Columns: datetime, open, high, low, close, volume, turnover
  vt_symbol format: "SH600519", "SZ000001", "BJ920000"
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
from loguru import logger

WORK_DIR = Path("/run/media/onai/MyDisk/Work")
SHARED_DATA = WORK_DIR / "shared_data"


def ticker_to_vt_symbol(ticker: str) -> str:
    """Convert database ticker to vnpy vt_symbol format.

    Ticker formats in our DB:
      - sh.600519 -> SH600519
      - sz.000001 -> SZ000001
      - bj.920000 -> BJ920000
    """
    ticker_lower = ticker.lower()
    if ticker_lower.startswith("sh."):
        return ticker_lower.replace("sh.", "").upper()
    elif ticker_lower.startswith("sz."):
        return ticker_lower.replace("sz.", "").upper()
    elif ticker_lower.startswith("bj."):
        return ticker_lower.replace("bj.", "").upper()
    else:
        return ticker.upper()


def export_vnpy_parquet(
    csv_dir: Path,
    dest_dir: Path,
    progress_interval: int = 500,
) -> dict:
    """Convert shared CSV files to vnpy daily parquet format.

    Args:
        csv_dir: Directory containing astock_daily_csv files (bj_920000.csv etc)
        dest_dir: Output directory for vnpy parquet files
        progress_interval: Log progress every N files

    Returns:
        dict with success/failed counts
    """
    dest_dir.mkdir(parents=True, exist_ok=True)

    success = 0
    failed: list[str] = []
    total_rows = 0

    csv_files = sorted(csv_dir.glob("*.csv"))
    csv_files = [f for f in csv_files if not f.name.startswith("__")]

    for idx, csv_file in enumerate(csv_files, 1):
        try:
            # Parse filename: bj_920000.csv → BJ920000
            stem = csv_file.stem  # e.g., "bj_920000"
            parts = stem.split("_", 1)
            if len(parts) != 2:
                continue
            exchange_prefix, code = parts  # "bj", "920000"
            vt_symbol = f"{exchange_prefix.upper()}{code}"

            # Read CSV
            df = pd.read_csv(csv_file)

            # Rename columns to vnpy format
            col_map = {}
            if "date" in df.columns:
                col_map["date"] = "datetime"
            if "open" in df.columns:
                col_map["open"] = "open"
            if "high" in df.columns:
                col_map["high"] = "high"
            if "low" in df.columns:
                col_map["low"] = "low"
            if "close" in df.columns:
                col_map["close"] = "close"
            if "volume" in df.columns:
                col_map["volume"] = "volume"
            if "amount" in df.columns:
                col_map["amount"] = "turnover"

            df = df.rename(columns=col_map)

            # Convert datetime
            if "datetime" in df.columns:
                df["datetime"] = pd.to_datetime(df["datetime"])

            # Select only needed columns
            required = ["datetime", "open", "high", "low", "close", "volume", "turnover"]
            available = [c for c in required if c in df.columns]
            df = df[available]

            # Add vt_symbol column for alpha dataset compatibility
            df["vt_symbol"] = vt_symbol

            # Sort by datetime
            df = df.sort_values("datetime").reset_index(drop=True)

            # Save as parquet
            out_path = dest_dir / f"{vt_symbol}.parquet"
            df.to_parquet(out_path, index=False)

            success += 1
            total_rows += len(df)

            if success % progress_interval == 0:
                logger.info(f"  Converted {success}/{len(csv_files)} files...")

        except Exception as e:
            failed.append(f"{csv_file.name}: {e}")

    return {
        "success": success,
        "failed": failed,
        "total_rows": total_rows,
        "output_dir": str(dest_dir),
    }


def main() -> None:
    csv_dir = SHARED_DATA / "astock_daily_csv"
    dest_dir = SHARED_DATA / "vnpy_daily"

    logger.info("📊 Exporting vnpy parquet data...")
    logger.info(f"   Source: {csv_dir}")
    logger.info(f"   Dest:   {dest_dir}")

    result = export_vnpy_parquet(csv_dir, dest_dir)

    logger.info(f"✅ Done: {result['success']} files, {result['total_rows']:,} rows")
    if result["failed"]:
        logger.warning(f"⚠️  Failed: {len(result['failed'])} files")
        for f in result["failed"][:5]:
            logger.warning(f"   - {f}")


if __name__ == "__main__":
    main()
