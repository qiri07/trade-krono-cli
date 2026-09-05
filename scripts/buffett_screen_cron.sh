#!/bin/bash
# 巴菲特六闸门全量筛选 — Cron 包装脚本
# 每天 16:10（交易日）执行增量拉取
#
# 安装 cron 任务:
#   crontab -e
#   添加: 10 16 * * 1-5 /path/to/trade-krono-cli/scripts/buffett_screen_cron.sh

set -euo pipefail

export PATH="/home/onai/.local/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
export PYTHONIOENCODING="utf-8"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
LOG_DIR="$PROJECT_ROOT/outputs/logs"
LOG_FILE="$LOG_DIR/buffett_screen_$(date +%Y%m%d_%H%M%S).log"

mkdir -p "$LOG_DIR"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 开始巴菲特六闸门全量筛选..." >> "$LOG_FILE"

cd "$PROJECT_ROOT"
export PYTHONPATH="$PROJECT_ROOT:$PYTHONPATH"
uv run python tests/buffett_screen_parallel.py >> "$LOG_FILE" 2>&1
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 筛选完成，状态: 成功" >> "$LOG_FILE"
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 筛选完成，状态: 失败 (exit=$EXIT_CODE)" >> "$LOG_FILE"
fi

exit $EXIT_CODE
