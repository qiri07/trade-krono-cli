#!/bin/bash
# CZSC 全市场缠论信号扫描 — Cron 包装脚本
# 每个交易日 16:20（收盘后）执行
#
# 安装 cron 任务:
#   crontab -e
#   添加: 20 16 * * 1-5 /path/to/trade-krono-cli/scripts/czsc_signal_scan_cron.sh

set -euo pipefail

export PATH="/home/onai/.local/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
export PYTHONIOENCODING="utf-8"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
LOG_DIR="$PROJECT_ROOT/outputs/logs"
LOG_FILE="$LOG_DIR/czsc_signal_scan_$(date +%Y%m%d_%H%M%S).log"

mkdir -p "$LOG_DIR"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 开始 CZSC 全市场信号扫描..." >> "$LOG_FILE"

cd "$PROJECT_ROOT"
export PYTHONPATH="$PROJECT_ROOT:${PYTHONPATH:-}"

# 必须用 .venv 里的 python（含 pandas 3.x 兼容 pickle）
uv run python scripts/czsc_universe_signal_scan.py --push 1 >> "$LOG_FILE" 2>&1
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 扫描完成，状态: 成功" >> "$LOG_FILE"
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 扫描完成，状态: 失败 (exit=$EXIT_CODE)" >> "$LOG_FILE"
fi

# 保留最近 30 天日志
find "$LOG_DIR" -name "czsc_signal_scan_*.log" -mtime +30 -delete

exit $EXIT_CODE