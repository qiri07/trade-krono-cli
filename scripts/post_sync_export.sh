#!/usr/bin/env bash
# Post-sync data export hook
# After sync-whitelist or fill_missing_and_today, exports shared data to RD-Agent format
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SHARED_DATA="/run/media/onai/MyDisk/Work/shared_data"

echo "📤 开始导出共享数据 → $SHARED_DATA"
cd "$PROJECT_ROOT"

# Export RD-Agent formats from pipeline cache
uv run python scripts/export_shared_data.py --format all --dest "$SHARED_DATA"

echo "✅ 共享数据导出完成"
echo ""
echo "📊 当前导出格式:"
echo "  RD-Agent     → ${SHARED_DATA}/astock_daily.parquet"
