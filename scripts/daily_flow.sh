#!/usr/bin/env bash
# daily_flow.sh — 一键执行每日 10 步投研流程
#
# 用法：
#   bash scripts/daily_flow.sh                 # 默认：同步 + 全量筛选 + 飞书推送
#   DRY_RUN=1 bash scripts/daily_flow.sh        # 只打印不执行
#   NO_PUSH=1 bash scripts/daily_flow.sh        # 跳过所有飞书推送
#   NO_SYNC=1 bash scripts/daily_flow.sh        # 跳过第 1 步数据同步
#   SKIP_ETF=1 bash scripts/daily_flow.sh       # 跳过 ETF 同步
#
# 各步骤（与 AGENTS.md「每日分析流程」一致）：
#   1. 数据同步    fill_missing_and_today.py + etf_history_sync.py
#   2. 巴菲特筛选  buffett_screen_parallel.py
#   3. 三线共振    multi_line_resonance.py
#   4. 多头排列    multi_scan.py
#   5. 白名单分析  daily_analysis.py
#   6. 双均线金叉  double_ma_golden_cross.py（完整模式）
#   10. MACD+均线  macd_ma_screener.py（第 9 步交集来源线之一，提前跑）
#   9. 综合交集汇总 combined_stock_analysis.py
#   11. 盘中扫描   intraday_scan.py（9:30-15:00 盘中可选，默认跑不推送）

set -uo pipefail

cd "$(dirname "$0")/.."

DRY_RUN="${DRY_RUN:-0}"
NO_PUSH="${NO_PUSH:-0}"
NO_SYNC="${NO_SYNC:-0}"
SKIP_ETF="${SKIP_ETF:-0}"

# 推送开关：NO_PUSH=1 时全部关闭
PUSH_FLAG=1
[ "$NO_PUSH" = "1" ] && PUSH_FLAG=0

run() {
    # 单步执行器：$1=步骤名，其余=命令
    local step="$1"; shift
    echo
    echo "════════════════════════════════════════════════════════"
    echo "▶ [$(date +%H:%M:%S)] $step"
    echo "  $*"
    echo "════════════════════════════════════════════════════════"
    if [ "$DRY_RUN" = "1" ]; then
        return 0
    fi
    if ! "$@"; then
        echo "✗ $step 失败（继续执行后续步骤）"
    fi
}

FAILED=0

# ── 1. 数据同步 ──────────────────────────────────────────────────────────
if [ "$NO_SYNC" != "1" ]; then
    run "1/10 数据同步（补齐失败 + 今日增量）" \
        uv run python scripts/fill_missing_and_today.py
    if [ "$SKIP_ETF" != "1" ]; then
        run "1b/10 ETF K 线同步（增量）" \
            uv run python scripts/etf_history_sync.py
    fi
else
    echo "跳过数据同步（NO_SYNC=1）"
fi

# ── 2. 巴菲特六闸门筛选（并行）────────────────────────────────────────────
run "2/10 巴菲特六闸门筛选（并行版）" \
    uv run python tests/buffett_screen_parallel.py

# ── 3. 三线共振 ──────────────────────────────────────────────────────────
run "3/10 三线共振扫描" \
    uv run python scripts/multi_line_resonance.py

# ── 4. 多头排列 ──────────────────────────────────────────────────────────
run "4/10 多头排列扫描" \
    uv run python scripts/multi_scan.py

# ── 5. 白名单分析 + AI + 飞书 ───────────────────────────────────────────
run "5/10 白名单分析（AI 核实 + 飞书推送）" \
    uv run python scripts/daily_analysis.py

# ── 6. 双均线金叉（完整模式：MA+PE+MACD+ROE+净利润）────────────────────
run "6/10 双均线金叉筛选（完整模式）" \
    uv run python scripts/double_ma_golden_cross.py

# ── 10. MACD+均线筛选（第 9 步 "macd_ma" 来源线）───────────────────────
run "7/10 MACD+均线筛选（macd_ma_screener）" \
    uv run python scripts/macd_ma_screener.py --push "$PUSH_FLAG"

# ── 9. 综合交集汇总（多线共振 + 技术面 + AI + 飞书）────────────────────
run "8/10 综合选股交集汇总（combined_stock_analysis）" \
    uv run python scripts/combined_stock_analysis.py --push "$PUSH_FLAG"

# ── 11. 盘中扫描（盘中可选，不推送）────────────────────────────────────
run "9/10 盘中技术信号扫描（intraday_scan）" \
    uv run python scripts/intraday_scan.py --push 0

# ── 共享数据导出（供 qlib/vnpy/RD-Agent 等下游项目）────────────────────
run "10/10 共享数据导出" \
    bash scripts/post_sync_export.sh

# ── 汇总 ────────────────────────────────────────────────────────────────
echo
echo "════════════════════════════════════════════════════════"
echo "每日流程执行完毕（$(date +%Y-%m-%d\ %H:%M:%S)）"
echo "结果目录：outputs/results/"
echo "════════════════════════════════════════════════════════"
exit 0
