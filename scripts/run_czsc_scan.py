#!/usr/bin/env python3
"""
CZSC 全市场信号扫描 — 统一入口脚本

分步运行，解决 pandas 3.x / 2.3 兼容性：
1. DB→parquet 转换：用 trade-krono-cli venv (pandas 3.0.5)
2. 信号计算：用 czsc venv (已装 czsc, pandas 2.3.2)

用法：
    python scripts/run_czsc_scan.py --asof 2026-10-30
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TRADE_KRONO_PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python"
CZSC_PYTHON = Path("/run/media/onai/MyDisk/Work/czsc/.venv/bin/python")
SCAN_SCRIPT = PROJECT_ROOT / "scripts" / "czsc_universe_signal_scan.py"
CONVERT_SCRIPT = PROJECT_ROOT / "scripts" / "czsc_convert_db.py"
LOG_DIR = PROJECT_ROOT / "outputs" / "logs"

CONVERT_TIMEOUT = 300
SCAN_TIMEOUT = 180


def run_convert(asof: str) -> tuple[int, int]:
    """在 trade-krono-cli venv 中运行转换步骤"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT)
    result = subprocess.run(
        [str(TRADE_KRONO_PYTHON), str(CONVERT_SCRIPT)],
        cwd=str(PROJECT_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=CONVERT_TIMEOUT,
    )
    if result.returncode != 0:
        raise RuntimeError(f"转换失败: {result.stderr}")
    # 解析输出 "Converted: X, Skipped: Y"
    for line in result.stdout.splitlines():
        if line.startswith("Converted:"):
            parts = line.split(",")
            converted = int(parts[0].split(":")[1].strip())
            skipped = int(parts[1].split(":")[1].strip())
            return converted, skipped
    return 0, 0


def run_scan(asof: str) -> dict:
    """在 czsc venv 中运行信号扫描"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT)
    result = subprocess.run(
        [
            str(CZSC_PYTHON), str(SCAN_SCRIPT),
            "--scan-only", "--asof", asof, "--push", "1",
        ],
        cwd=str(PROJECT_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=SCAN_TIMEOUT,
    )
    if result.returncode != 0:
        raise RuntimeError(f"扫描失败: {result.stderr}")
    # 解析输出
    lines = result.stdout.strip().splitlines()
    return {
        "bi_ma": next((line for line in lines if line.startswith("bi_ma:")), "").split(":")[1].strip(),
        "third_buy": next((line for line in lines if line.startswith("third_buy:")), "").split(":")[1].strip(),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof", default=None, help="截面日期 YYYY-MM-DD，默认今天")
    ap.add_argument("--skip-convert", action="store_true", help="跳过转换，只扫描")
    args = ap.parse_args()

    asof = args.asof or datetime.now().strftime("%Y-%m-%d")
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    print(f"[{datetime.now()}] CZSC 全市场信号扫描开始 (asof={asof})")
    print(f"  Trade-krono python: {TRADE_KRONO_PYTHON}")
    print(f"  CZSC python: {CZSC_PYTHON}")

    # 1) 转换
    if not args.skip_convert:
        print("[1/2] DB → parquet 增量转换 (trade-krono venv)...")
        t0 = time.perf_counter()
        converted, skipped = run_convert(asof)
        print(f"     新转换 {converted} 只，跳过 {skipped} 只，耗时 {time.perf_counter() - t0:.1f}s")
    else:
        print("[1/2] 跳过转换步骤")
        converted = skipped = 0

    # 2) 扫描
    print("[2/2] 全市场信号扫描 (czsc venv)...")
    t0 = time.perf_counter()
    scan_results = run_scan(asof)
    print(f"     {scan_results['bi_ma']}")
    print(f"     {scan_results['third_buy']}")
    print(f"     扫描耗时 {time.perf_counter() - t0:.1f}s")

    # 汇总
    summary = {
        "date": asof,
        "timestamp": datetime.now().isoformat(),
        "conversion": {"converted": converted, "skipped": skipped},
        "presets": scan_results,
    }
    summary_path = PROJECT_ROOT / "outputs" / "czsc_signal_scan" / f"summary_{asof}.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[done] 汇总: {summary_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
