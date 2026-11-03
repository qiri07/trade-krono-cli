#!/bin/bash
# CZSC 全市场缠论信号扫描 — 全流程包装脚本
# 步骤1：DB → parquet 导出（czsc_convert_db.py）
# 步骤2：全市场信号扫描 + 飞书推送（czsc_universe_signal_scan.py --scan-only）
#
# 用法：bash scripts/czsc_full_scan.sh [asof=YYYY-MM-DD] [push=0|1]

set -euo pipefail

export PATH="/home/onai/.local/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
export PYTHONIOENCODING="utf-8"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

ASOF="${1:-$(date +%Y-%m-%d)}"
PUSH_FLAG="${2:-0}"

mkdir -p "$PROJECT_ROOT/outputs/logs"
cd "$PROJECT_ROOT"
export PYTHONPATH="$PROJECT_ROOT:${PYTHONPATH:-}"

# 必须用 .venv 里的 python（含 pandas 3.x 兼容 pickle）
echo "[$(date '+%Y-%m-%d %H:%M:%S')] [1/2] DB → parquet 导出（czsc_convert_db.py）" 
uv run python scripts/czsc_convert_db.py

echo "[$(date '+%Y-%m-%d %H:%M:%S')] [2/2] 缠论信号扫描 + 飞书推送（czsc_universe_signal_scan.py --scan-only asof=$ASOF push=$PUSH_FLAG）"
uv run python scripts/czsc_universe_signal_scan.py --scan-only --asof "$ASOF" --push "$PUSH_FLAG"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] CZSC 全流程完成"
