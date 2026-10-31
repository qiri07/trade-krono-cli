#!/usr/bin/env bash
# daily_flow.sh — 一键执行每日投研流程（薄壳，实际调度在 scripts/flow_runner.py）
#
# 用法（与历史保持兼容，env flag 透传给 flow_runner）：
#   bash scripts/daily_flow.sh                 # 默认：全部 13 个 step 串行（export 默认禁用）
#   DRY_RUN=1 bash scripts/daily_flow.sh       # 只打印不执行
#   NO_PUSH=1 bash scripts/daily_flow.sh        # 跳过飞书推送（PUSH_FLAG=0）
#   NO_SYNC=1 bash scripts/daily_flow.sh        # 跳过数据同步类 step
#   SKIP_ETF=1 bash scripts/daily_flow.sh       # 跳过 ETF 同步
#   EXPORT=1 bash scripts/daily_flow.sh         # 额外执行共享数据导出
#   RESUME=1 bash scripts/daily_flow.sh        # 断点续传：跳过已成功 step，从失败处继续
#
# 单独执行某个 step：
#   uv run python scripts/flow_runner.py run --step check
#   uv run python scripts/flow_runner.py run --step sync
#   uv run python scripts/flow_runner.py run --all --enable export

set -uo pipefail
cd "$(dirname "$0")/.."

# 透传 env flag（flow_runner.py 在 import 时读取这些变量）
export DRY_RUN="${DRY_RUN:-0}"
export NO_PUSH="${NO_PUSH:-0}"
export NO_SYNC="${NO_SYNC:-0}"
export SKIP_ETF="${SKIP_ETF:-0}"
export EXPORT="${EXPORT:-0}"
export RESUME="${RESUME:-0}"

# 默认 DRY_RUN=1 时走 dry-run；否则按 env 决定
RUN_ARGS=(run --all)
[ "$DRY_RUN" = "1" ] && RUN_ARGS+=(--dry-run)
[ "$RESUME" = "1" ] && RUN_ARGS+=(--resume)

uv run python scripts/flow_runner.py "${RUN_ARGS[@]}"

echo
echo "════════════════════════════════════════════════════════"
echo "每日流程执行完毕（$(date +%Y-%m-%d\ %H:%M:%S)）"
echo "结果目录：outputs/results/"
echo "════════════════════════════════════════════════════════"
exit $?
