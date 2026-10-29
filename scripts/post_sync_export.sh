#!/usr/bin/env bash
# Post-sync data export hook
# After sync-whitelist or fill_missing_and_today, exports shared data to RD-Agent format
#
# 导出默认禁用，需传 --enable 才执行：
#   bash scripts/post_sync_export.sh            # 默认跳过导出
#   bash scripts/post_sync_export.sh --enable    # 实际执行导出
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SHARED_DATA="$PROJECT_ROOT/data/shared_data"

ENABLE="${1:-}"
if [ "$ENABLE" != "--enable" ]; then
    echo "⏭️  导出已默认禁用（未传 --enable），跳过导出。"
    exit 0
fi

echo "📤 开始导出共享数据 → $SHARED_DATA"
cd "$PROJECT_ROOT"

# Export RD-Agent formats from pipeline cache
uv run python scripts/export_shared_data.py --format all --dest "$SHARED_DATA" --enable

echo "✅ 共享数据导出完成"
echo ""
echo "📊 当前导出格式:"
echo "  RD-Agent     → ${SHARED_DATA}/astock_daily.parquet"
