#!/usr/bin/env bash
# Post-sync data export hook
# After sync-whitelist or fill_missing_and_today, exports shared data to all project formats
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SHARED_DATA="/run/media/onai/MyDisk/Work/shared_data"

echo "📤 开始导出共享数据 → $SHARED_DATA"
cd "$PROJECT_ROOT"

# Export all formats from pipeline cache
uv run python scripts/export_shared_data.py --format all --dest "$SHARED_DATA"

# Export vnpy parquet format
uv run python scripts/export_vnpy_data.py

echo "✅ 共享数据导出完成"
echo ""
echo "📊 各项目数据映射:"
echo "  Kronos       → ${SHARED_DATA}/astock_daily_csv (已 symlink)"
echo "  TradingAgents → ~/.tradingagents/cache (已 symlink)"
echo "  qlib         → ${SHARED_DATA}/qlib_data (已 symlink)"
echo "  backtrader   → ${HOME}/Work/backtrader/datas/astock_daily_csv (已 symlink)"
echo "  vnpy         → ${SHARED_DATA}/vnpy_daily (已 symlink)"
echo "  RD-Agent     → ${SHARED_DATA}/qlib_data + daily_pv_full.{parquet,h5} (已同步)"
