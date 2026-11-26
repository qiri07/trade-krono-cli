#!/usr/bin/env python3
"""港股每日筛选全流程脚本。

参考 A股流程结构，实现港股专属的三步筛选流程：
  1. sync_hk      — 港股K线增量同步
  2. metadata_hk  — 港股基本面元数据刷新
  3. hk_screen    — 港股每日筛选（含JSON落盘+飞书推送）

用法：
    uv run python scripts/hk_screen_full.py                       # 运行全部三步
    uv run python scripts/hk_screen_full.py --step hk_screen      # 仅运行筛选
    uv run python scripts/hk_screen_full.py --step metadata_hk     # 仅运行元数据刷新
    uv run python scripts/hk_screen_full.py --step sync_hk        # 仅运行K线同步
    uv run python scripts/hk_screen_full.py --no-push             # 跳过飞书推送
    uv run python scripts/hk_screen_full.py --top 20              # 只输出前20名
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ── 导入 hk_daily_screen 主函数（在路径就绪后）──────────────────
from scripts.hk_daily_screen import main as screen_main  # type: ignore  # noqa: E402

# ── 手动命令行参数解析（仅认可 --step, --no-push, --top）────────
step: str | None = None
no_push = False
top_arg: str | None = None

raw_args = sys.argv[1:]  # 去掉脚本名
i = 0
while i < len(raw_args):
    if raw_args[i] == "--step" and i + 1 < len(raw_args):
        step = raw_args[i + 1]
        i += 2
    elif raw_args[i] == "--no-push":
        no_push = True
        i += 1
    elif raw_args[i] == "--top" and i + 1 < len(raw_args):
        top_arg = raw_args[i + 1]
        i += 2
    else:
        i += 1

# 配置环境变量
os.environ.setdefault("PUSH_FLAG", "0" if no_push else "1")
if top_arg is not None:
    os.environ["HK_SCREEN_TOP"] = top_arg

# 剩余参数直接传给 hk_daily_screen.main
remaining_args = raw_args[i:]  # 过滤掉已解析的 --step/--no-push/--top


# ── 单步入口函数 ────────────────────────────────────────────────
def _run_sync_hk() -> int:
    """运行港股K线同步步骤。"""
    from scripts.full_hk_history_sync import main as sync_main  # type: ignore
    return sync_main()


def _run_metadata_hk() -> int:
    """运行港股元数据刷新步骤。"""
    from scripts.fetch_hk_metadata import main as meta_main  # type: ignore
    return meta_main()


def _run_hk_screen() -> int:
    """运行港股筛选步骤。"""
    return screen_main(remaining_args)


# ── 主入口 ──────────────────────────────────────────────────────
def main() -> None:
    """入口：根据 --step 运行对应流程步骤，默认运行全部三步。"""
    if step == "sync_hk":
        print("=== 步骤 1/3：港股K线同步 ===")
        exit(_run_sync_hk())
    elif step == "metadata_hk":
        print("=== 步骤 2/3：港股元数据刷新 ===")
        exit(_run_metadata_hk())
    elif step == "hk_screen":
        print("=== 步骤 3/3：港股每日筛选 ===")
        exit(_run_hk_screen())
    else:
        # 默认运行全部：sync_hk → metadata_hk → hk_screen
        print("=== 步骤 1/3：港股K线同步 ===")
        r1 = _run_sync_hk()
        if r1 != 0:
            print("sync_hk 失败，终止流程。")
            sys.exit(r1)
        print("=== 步骤 2/3：港股元数据刷新 ===")
        r2 = _run_metadata_hk()
        if r2 != 0:
            print("metadata_hk 失败，终止流程。")
            sys.exit(r2)
        print("=== 步骤 3/3：港股每日筛选 ===")
        r3 = _run_hk_screen()
        sys.exit(r3)


if __name__ == "__main__":
    main()

