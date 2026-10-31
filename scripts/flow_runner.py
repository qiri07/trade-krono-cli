#!/usr/bin/env python3
"""统一 step 注册表 + 单步/全量串行执行器。

设计目标：
  · 每个步骤注册为 dataclass，统一调度
  · `run --step X` 单独执行；`run --all` 串行执行全部默认开启 step
  · `--enable/--disable` 临时开关；`--dry-run` 只打印
  · 兼容旧 `daily_flow.sh` 的 env flag（NO_PUSH/NO_SYNC/SKIP_ETF/EXPORT）

用法：
  uv run python scripts/flow_runner.py list
  uv run python scripts/flow_runner.py run --step check
  uv run python scripts/flow_runner.py run --step sync
  uv run python scripts/flow_runner.py run --all
  uv run python scripts/flow_runner.py run --all --enable export
  uv run python scripts/flow_runner.py run --all --disable intraday
  uv run python scripts/flow_runner.py run --all --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 透传 env flag（与 daily_flow.sh 历史行为一致）
DRY_RUN = os.getenv("DRY_RUN", "0") == "1"
NO_PUSH = os.getenv("NO_PUSH", "0") == "1"
NO_SYNC = os.getenv("NO_SYNC", "0") == "1"
SKIP_ETF = os.getenv("SKIP_ETF", "0") == "1"
EXPORT = os.getenv("EXPORT", "0") == "1"

PUSH_FLAG = 0 if NO_PUSH else 1

DEFAULT_STEP_TIMEOUT_SECONDS = 3600


def checkpoint_path() -> Path:
    """返回当天 flow checkpoint 文件路径（results 目录，与测试隔离保持一致）。"""
    results_dir = Path(
        os.getenv("TRADING_KRONO_RESULTS_DIR", str(PROJECT_ROOT / "outputs" / "results"))
    )
    return results_dir / f"flow_checkpoint_{time.strftime('%Y-%m-%d')}.json"


def load_checkpoint() -> dict[str, str]:
    """加载当天已记录的 step 执行状态（缺失或损坏时返回空字典）。"""
    path = checkpoint_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    steps = data.get("steps")
    if not isinstance(steps, dict):
        return {}
    return {str(k): str(v) for k, v in steps.items()}


def save_checkpoint(statuses: dict[str, str]) -> None:
    """保存当天 step 执行状态 checkpoint（断点记录）。"""
    path = checkpoint_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"date": time.strftime("%Y-%m-%d"), "steps": statuses}, ensure_ascii=False),
        encoding="utf-8",
    )


@dataclass(frozen=True)
class Step:
    """单个可执行 step 定义。

    Attributes:
        name: step 标识（如 "sync"、"buffett"），用于 `--step` 与 `--enable/--disable`
        description: 人类可读说明
        command: 执行命令（list[str]，shell=False）
        enabled_by_default: 默认是否纳入 `--all` 串行（False 时需 `--enable` 显式开启）
        depends_on: 前置 step name 列表（`--all` 时保证顺序）
    """

    name: str
    description: str
    command: list[str]
    enabled_by_default: bool = True
    depends_on: list[str] = field(default_factory=list)
    timeout: int = DEFAULT_STEP_TIMEOUT_SECONDS

    def to_command_str(self) -> str:
        return " ".join(self.command)


# ── Step 注册表（按依赖序）────────────────────────────────────────────
STEPS: list[Step] = [
    Step(
        name="check",
        description="数据现状检查 + 补拉计划生成（check_data_status.py）",
        command=["uv", "run", "python", "scripts/check_data_status.py"],
        enabled_by_default=True,
    ),
    Step(
        name="sync",
        description="K 线增量补拉（fill_missing_and_today.py + etf_history_sync.py）",
        command=[
            "uv",
            "run",
            "python",
            "scripts/fill_missing_and_today.py",
            "--plan",
            f"outputs/results/sync_plan_{time.strftime('%Y%m%d')}.json",
        ],
        enabled_by_default=not NO_SYNC,
        depends_on=["check"],
        timeout=7200,
    ),
    Step(
        name="sync_etf",
        description="ETF K 线同步（etf_history_sync.py，增量）",
        command=["uv", "run", "python", "scripts/etf_history_sync.py"],
        enabled_by_default=(not NO_SYNC and not SKIP_ETF),
        depends_on=["sync"],
        timeout=7200,
    ),
    Step(
        name="metadata",
        description="参考数据 TTL 刷新（fetch_reference_data.py）",
        command=["uv", "run", "python", "scripts/fetch_reference_data.py"],
        enabled_by_default=not NO_SYNC,
        depends_on=["sync"],
    ),
    Step(
        name="buffett",
        description="巴菲特六闸门筛选（并行版）",
        command=["uv", "run", "python", "tests/buffett_screen_parallel.py"],
        enabled_by_default=True,
        depends_on=["sync"],
        timeout=5400,
    ),
    Step(
        name="resonance",
        description="三线共振扫描（multi_line_resonance.py）",
        command=["uv", "run", "python", "scripts/multi_line_resonance.py"],
        enabled_by_default=True,
        depends_on=["sync"],
    ),
    Step(
        name="multi_head",
        description="多头排列扫描（multi_scan.py）",
        command=["uv", "run", "python", "scripts/multi_scan.py"],
        enabled_by_default=True,
        depends_on=["sync"],
    ),
    Step(
        name="whitelist",
        description="白名单分析 + AI 核实 + 飞书推送（daily_analysis.py）",
        command=["uv", "run", "python", "scripts/daily_analysis.py"],
        enabled_by_default=True,
        depends_on=["sync"],
        timeout=3600,
    ),
    Step(
        name="double_ma",
        description="双均线金叉筛选（完整模式）",
        command=["uv", "run", "python", "scripts/double_ma_golden_cross.py"],
        enabled_by_default=True,
        depends_on=["sync"],
    ),
    Step(
        name="macd_ma",
        description="MACD+均线筛选（macd_ma_screener.py）",
        command=[
            "uv",
            "run",
            "python",
            "scripts/macd_ma_screener.py",
            "--push",
            str(PUSH_FLAG),
        ],
        enabled_by_default=True,
        depends_on=["sync"],
    ),
    Step(
        name="combined",
        description="综合选股交集汇总（combined_stock_analysis.py）",
        command=[
            "uv",
            "run",
            "python",
            "scripts/combined_stock_analysis.py",
            "--push",
            str(PUSH_FLAG),
        ],
        enabled_by_default=True,
        depends_on=["sync"],
    ),
    Step(
        name="intraday",
        description="盘中技术信号扫描（intraday_scan.py，不推送）",
        command=["uv", "run", "python", "scripts/intraday_scan.py", "--push", "0"],
        enabled_by_default=True,
        depends_on=["sync"],
    ),
    Step(
        name="export",
        description="共享数据导出（post_sync_export.sh）——默认禁用，需 EXPORT=1 或 --enable export",
        command=["bash", "scripts/post_sync_export.sh"],
        enabled_by_default=EXPORT,
        depends_on=["sync"],
        timeout=3600,
    ),
]


def get_step(name: str) -> Step | None:
    """按 name 查找 step；未注册返回 None。"""
    for s in STEPS:
        if s.name == name:
            return s
    return None


def all_step_names() -> list[str]:
    return [s.name for s in STEPS]


def run_step(step: Step, dry_run: bool = False) -> int:
    """执行单个 step；返回 exit code（dry_run 时返回 0）。"""
    print(f"\n▶ [{step.name}] {step.description}")
    print(f"  $ {step.to_command_str()}")
    if dry_run or DRY_RUN:
        print("  🧪 DRY-RUN: 跳过执行")
        return 0
    try:
        result = subprocess.run(
            step.command,
            cwd=str(PROJECT_ROOT),
            shell=False,
            timeout=step.timeout,
        )
    except subprocess.TimeoutExpired:
        print(f"  ⏱️  [{step.name}] 超时（>{step.timeout}s），按失败处理")
        return 124
    if result.returncode != 0:
        print(f"  ✗ [{step.name}] 失败（exit {result.returncode}）")
    return result.returncode


def resolve_enabled_steps(
    enable: list[str] | None = None,
    disable: list[str] | None = None,
) -> list[Step]:
    """根据 --enable/--disable 计算最终要执行的 step 列表。

    规则：
      · 默认纳入 `enabled_by_default=True` 的 step
      · `--enable X` 强制纳入 X（即使默认禁用）
      · `--disable X` 强制排除 X
      · 按 depends_on 拓扑序执行
    """
    enable_set = set(enable or [])
    disable_set = set(disable or [])

    selected: dict[str, Step] = {}
    for s in STEPS:
        if s.name in disable_set:
            continue
        if s.enabled_by_default or s.name in enable_set:
            selected[s.name] = s

    # 拓扑排序：按 depends_on 保证前置 step 先跑
    order: list[Step] = []
    placed: set[str] = set()
    remaining = dict(selected)
    while remaining:
        progressed = False
        for name in list(remaining.keys()):
            deps = remaining[name].depends_on
            if all(d in placed or d not in selected for d in deps):
                order.append(remaining[name])
                placed.add(name)
                del remaining[name]
                progressed = True
                break
        if not progressed:
            # 循环依赖兜底：直接按注册序追加
            order.extend(remaining.values())
            break
    return order


def cmd_list() -> int:
    """打印全部 step（含默认启用状态）。"""
    print(f"{'step':<12} {'default':<10} {'description'}")
    print("-" * 70)
    for s in STEPS:
        default = "enabled" if s.enabled_by_default else "DISABLED"
        print(f"{s.name:<12} {default:<10} {s.description}")
    print()
    print(
        f"共 {len(STEPS)} 个 step；`run --all` 默认执行 {sum(1 for s in STEPS if s.enabled_by_default)} 个"
    )
    return 0


def cmd_run(
    step: str | None = None,
    all_steps: bool = False,
    enable: list[str] | None = None,
    disable: list[str] | None = None,
    dry_run: bool = False,
    strict: bool = False,
    resume: bool = False,
) -> int:
    """run 子命令：单步或全量串行。

    断点续传（resume）语义：
      - 默认（resume=False）：全量串行执行所有启用 step；前置依赖失败时跳过下游 step，
        避免用脏数据推导后续结论；单个 step 失败继续执行其余独立 step
      - resume=True：跳过当天 checkpoint 中标记为 success 的 step，从首个未成功
        的 step 开始执行；任一 step 失败时立即停止（断点），等待下次 resume 重试
    """
    if all_steps:
        targets = resolve_enabled_steps(enable=enable, disable=disable)
        if not targets:
            print("没有可执行的 step（全部被禁用？）")
            return 1
        statuses: dict[str, str] = {}
        if resume:
            statuses = load_checkpoint()
            pending = [s for s in targets if statuses.get(s.name) != "success"]
            if not pending:
                print("✅ 断点续传：当天所有启用 step 均已成功，无待执行 step")
                return 0
            targets = pending
        print(f"▶ 串行执行 {len(targets)} 个 step: {[s.name for s in targets]}")
        failed: list[str] = []
        failed_set: set[str] = set()
        for s in targets:
            if any(d in failed_set for d in s.depends_on):
                # 前置 step 已失败 → 跳过本 step（避免用脏数据推导后续结论）
                print(
                    f"  ⏭️  跳过 [{s.name}]：前置 step 失败（{sorted(d for d in s.depends_on if d in failed_set)}）"
                )
                continue
            rc = run_step(s, dry_run=dry_run)
            statuses[s.name] = "success" if rc == 0 else "failed"
            if not dry_run:
                save_checkpoint(statuses)
            if rc != 0:
                failed.append(s.name)
                failed_set.add(s.name)
                if strict or resume:
                    print(f"  ✋ 断点停止于 [{s.name}]，后续 step 未执行")
                    return rc
        if failed:
            print(f"\n❌ 失败 step: {failed}（继续执行完成）")
            return 1
        print(f"\n✅ 全部 {len(targets)} 个 step 执行完成")
        return 0

    if not step:
        print("请指定 --step <name> 或 --all")
        return 2

    single: Step | None = get_step(step)
    if single is None:
        print(f"未知 step: {step}（可用: {all_step_names()}）")
        return 2
    rc = run_step(single, dry_run=dry_run)
    if not dry_run:
        save_checkpoint({step: "success" if rc == 0 else "failed"})
    return rc


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="统一 step 注册表执行器")
    sub = parser.add_subparsers(dest="cmd")

    p_list = sub.add_parser("list", help="列出全部 step")

    def _list(_args: argparse.Namespace) -> int:
        return cmd_list()

    p_list.set_defaults(func=_list)

    p_run = sub.add_parser("run", help="执行 step（单步或全量串行）")
    p_run.add_argument("--step", default=None, help="执行单个 step（name）")
    p_run.add_argument("--all", action="store_true", help="串行执行全部默认开启 step")
    p_run.add_argument("--enable", default="", help="强制启用默认关闭 step（逗号分隔）")
    p_run.add_argument("--disable", default="", help="强制排除 step（逗号分隔）")
    p_run.add_argument("--dry-run", action="store_true", help="只打印命令不执行")
    p_run.add_argument(
        "--strict",
        action="store_true",
        help="首错即停（--all 时生效）",
    )
    p_run.add_argument(
        "--resume",
        action="store_true",
        help="断点续传：跳过当天已成功 step，从失败/未执行处继续",
    )

    def _run(args: argparse.Namespace) -> int:
        enable_list = [x for x in args.enable.split(",") if x]
        disable_list = [x for x in args.disable.split(",") if x]
        return cmd_run(
            step=args.step,
            all_steps=args.all,
            enable=enable_list,
            disable=disable_list,
            dry_run=args.dry_run,
            strict=args.strict,
            resume=args.resume,
        )

    p_run.set_defaults(func=_run)
    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()
    if not hasattr(args, "func"):
        parser.print_help()
        return 2
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
