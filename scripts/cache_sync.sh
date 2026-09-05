#!/bin/bash
# A 股数据缓存检查与同步 - Cron 包装脚本
# 每天 10:00 和 15:30 执行
#
# 安装 cron 任务:
#   crontab -e
#   添加: 0 10,30 15 * * 1-5 /path/to/trade-krono-cli/scripts/cache_sync.sh

set -euo pipefail

# 确保 cron 环境能找到 uv 和其他工具
export PATH="/home/onai/.local/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
export PYTHONIOENCODING="utf-8"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
LOG_DIR="$PROJECT_ROOT/outputs/logs"
LOG_FILE="$LOG_DIR/cache_sync_$(date +%Y%m%d).log"

# 确保日志目录存在
mkdir -p "$LOG_DIR"

# 记录开始时间
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 开始检查数据缓存..." >> "$LOG_FILE"

# 执行检查与同步
cd "$PROJECT_ROOT"
uv run python scripts/check_and_sync_cache.py >> "$LOG_FILE" 2>&1
EXIT_CODE=$?

# 记录结果
if [ $EXIT_CODE -eq 0 ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 检查完成，状态: 成功" >> "$LOG_FILE"
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 检查完成，状态: 失败 (exit=$EXIT_CODE)" >> "$LOG_FILE"
fi

exit $EXIT_CODE
