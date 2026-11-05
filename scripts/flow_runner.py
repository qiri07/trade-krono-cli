#!/usr/bin/env python3
"""统一 step 注册表 + 基于 depends_on 的拓扑分层并行执行器。

设计目标：
  · 每个步骤注册为 dataclass，统一调度
  · `run --step X` 单独执行；`run --all` 按 depends_on 拓扑分层并行执行
    （同层互不依赖，可并行；层间串行）
  · `--enable/--disable` 临时开关；`--dry-run` 只打印
  · 兼容旧 `daily_flow.sh` 的 env flag（NO_PUSH/NO_SYNC/SKIP_ETF/EXPORT）

用法：
  uv run python scripts/flow_runner.py list
  uv run python scripts/flow_runner.py run --step check
  uv run python scripts/flow_runner.py run --step sync
  uv run python scripts/flow_runner.py run --all
  uv run python scripts/flow_runner.py run --all --enable export
  uv run python scripts/flow_runner.py run --all --disable intraday
  uv run python scripts/flow_runner.py run --all --disable backup   # 关闭统一备份（下游各自备份）
  uv run python scripts/flow_runner.py run --all --dry-run

备份策略：`--all` 时由首个 `backup` step 统一做一次前置备份，`sync` / `sync_etf`
会自动追加 `--no-backup`，避免一轮流程重复拷贝 ~1.1 GB 的 pipeline_cache.db；
单独执行 `--step sync` 或 `--disable backup` 时，各 step 仍会自行备份。

并发与限流说明：
  · `sync`（K 线增量补拉）会写入 pipeline_cache.db，与 `sync_etf`、`whitelist`
    的缓存写入可能并发冲突。kline.py 的 set_kline 已通过 fcntl 文件锁
    实现跨进程写入串行化（见 trade_krono_cli/cache/kline.py sqlite_write_lock）。
  · Nemotron AI API 调用（buffett/whitelist/combined）跨进程共享限流器，
    见 trade_krono_cli/llm_rate_limiter.py wait_for_rate_limit_cross_process()。
  · 同一层内并发数由 max_workers 控制（默认 8），DB 写入密集型 step 建议
    单独或低并发执行。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
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

# 由 backup step 统一备份后，这些自带备份的 step 追加 --no-backup，保证一轮流程只备一次
BACKUP_STEP_NAME = "backup"
STEPS_WITH_AUTO_BACKUP = frozenset({"sync", "sync_etf"})


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
        name="backup",
        description=(
            "写入前数据备份（backup_cache.py；本轮只备一次，"
            "sync/sync_etf 将自动跳过各自备份）"
        ),
        command=["uv", "run", "python", "scripts/backup_cache.py"],
        enabled_by_default=True,
        timeout=1800,
    ),
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
        depends_on=["check", "backup"],
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
        description="白名单分析 + AI 核实 + 飞书推送（scripts.daily_analysis）",
        command=["uv", "run", "python", "-m", "scripts.daily_analysis"],
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
        name="czsc",
        description="缠论信号全市场扫描（先 czsc_convert_db.py DB→parquet 导出，再 czsc_universe_signal_scan.py 扫描+飞书推送）",
        command=[
            "bash",
            "scripts/czsc_full_scan.sh",
            time.strftime("%Y-%m-%d"),
            str(PUSH_FLAG),
        ],
        enabled_by_default=True,
        depends_on=["sync"],
        timeout=3600,
    ),
    Step(
        name="intraday",
        description="盘中技术信号扫描（intraday_scan.py，不推送）",
        command=["uv", "run", "python", "scripts/intraday_scan.py", "--push", "0"],
        enabled_by_default=True,
        depends_on=["sync"],
    ),
    Step(
        name="combined",
        description="综合选股交集汇总（combined_stock_analysis.py，需其他选股步骤产出文件）",
        command=[
            "uv",
            "run",
            "python",
            "scripts/combined_stock_analysis.py",
            "--push",
            str(PUSH_FLAG),
        ],
        enabled_by_default=True,
        depends_on=[
            "sync",
            "buffett",
            "resonance",
            "multi_head",
            "whitelist",
            "double_ma",
            "macd_ma",
            "czsc",
            "intraday",
        ],
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


def _suppress_auto_backup(step: Step) -> Step:
    """给自带备份的 step 追加 `--no-backup`（本轮已由 backup step 统一备份）。

    Args:
        step: 待处理 step。

    Returns:
        追加 `--no-backup` 后的新 Step；不需要/已追加时原样返回。
    """
    if step.name not in STEPS_WITH_AUTO_BACKUP or "--no-backup" in step.command:
        return step
    return replace(step, command=[*step.command, "--no-backup"])


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


def _topo_layers(steps: list[Step]) -> list[list[Step]]:
    """基于 depends_on 进行拓扑分层。

    同一层的步骤互不直接依赖，可并行执行；层之间串行。
    返回 [[step, step, ...], [step, ...], ...]。

    若存在循环依赖，剩余未放入任何层的 step 会按注册序追加到最后一层。
    """
    step_map = {s.name: s for s in steps}
    in_degree = {s.name: 0 for s in steps}
    dependents: dict[str, list[str]] = {s.name: [] for s in steps}
    for s in steps:
        for d in s.depends_on:
            if d in step_map:
                in_degree[s.name] += 1
                dependents[d].append(s.name)

    layers: list[list[Step]] = []
    current = [name for name, deg in in_degree.items() if deg == 0]
    while current:
        layers.append([step_map[name] for name in current])
        next_current: list[str] = []
        for name in current:
            for dep_name in dependents[name]:
                in_degree[dep_name] -= 1
                if in_degree[dep_name] == 0:
                    next_current.append(dep_name)
        current = next_current

    # 环依赖兜底：剩余未放入任何层的 step 按注册序追加到最后一层
    placed = {s.name for layer in layers for s in layer}
    remaining = [s for s in steps if s.name not in placed]
    if remaining:
        layers.append(remaining)

    return layers


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
    """run 子命令：单步或全量拓扑分层并行。

    断点续传（resume）语义：
      - 默认（resume=False）：执行所有启用 step，按 depends_on 拓扑分层并行；
        前置依赖失败时跳过下游 step，避免用脏数据推导后续结论；
        单个 step 失败继续执行其余独立 step
      - resume=True：跳过当天 checkpoint 中标记为 success 的 step，从首个未成功
        的 step 开始执行；任一 step 失败时立即停止（断点），等待下次 resume 重试

    备份语义：backup step 在本次执行计划内（或当天已成功）时，sync / sync_etf
    自动追加 `--no-backup`，保证一轮流程只做一次前置备份。
    """
    if all_steps:
        targets = resolve_enabled_steps(enable=enable, disable=disable)
        if not targets:
            print("没有可执行的 step（全部被禁用？）")
            return 1
        statuses: dict[str, str] = {}
        # 本轮是否已有统一备份：backup step 在本次执行计划内，或当天 checkpoint 已记录成功
        backup_done = any(s.name == BACKUP_STEP_NAME for s in targets)
        if resume:
            statuses = load_checkpoint()
            backup_done = backup_done or statuses.get(BACKUP_STEP_NAME) == "success"
            pending_names = {s.name for s in targets if statuses.get(s.name) != "success"}
            if not pending_names:
                print("✅ 断点续传：当天所有启用 step 均已成功，无待执行 step")
                return 0
            targets = [s for s in targets if s.name in pending_names]
        if backup_done:
            # 一轮流程只保留一份前置备份：下游自带备份的 step 不再各自备份
            targets = [_suppress_auto_backup(s) for s in targets]
        print(f"▶ 分层并行执行 {len(targets)} 个 step")
        # 基于 depends_on 拓扑分层：同层互不依赖，可并行；层间串行
        layers = _topo_layers(targets)
        for i, layer in enumerate(layers, 1):
            layer_names = [s.name for s in layer]
            print(f"  第 {i} 层: {layer_names}")

        failed_set: set[str] = set()
        first_failure_rc: int | None = None
        for layer_idx, layer in enumerate(layers, 1):
            # 过滤掉依赖已失败 step 的 step（避免用脏数据推导后续结论）
            runnable = [
                s for s in layer if not any(d in failed_set for d in s.depends_on)
            ]
            skipped_in_layer = [s for s in layer if s not in runnable]
            for s in skipped_in_layer:
                print(
                    f"  ⏭️  跳过 [{s.name}]：前置 step 失败（{sorted(d for d in s.depends_on if d in failed_set)}）"
                )
            if not runnable:
                continue

            print(
                f"\n▶ 第 {layer_idx} 层并行执行 {len(runnable)} 个 step: "
                f"{[s.name for s in runnable]}"
            )
            layer_results: dict[str, int] = {}
            max_workers = min(len(runnable), 8)
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(run_step, s, dry_run=dry_run): s for s in runnable
                }
                for future in as_completed(futures):
                    s = futures[future]
                    rc = future.result()
                    layer_results[s.name] = rc
                    statuses[s.name] = "success" if rc == 0 else "failed"
                    if rc != 0:
                        failed_set.add(s.name)
                        if first_failure_rc is None:
                            first_failure_rc = rc
            if not dry_run:
                save_checkpoint(statuses)

            # 打印本层结果
            for s in runnable:
                rc = layer_results[s.name]
                print(f"  {'✅' if rc == 0 else '❌'} [{s.name}] exit {rc}")

            # strict 模式：首错即停，不执行后续层
            if strict and failed_set:
                print(
                    f"  ✋ 严格模式：首个失败于 [{sorted(failed_set)[0]}]，停止后续层"
                )
                return first_failure_rc or 1

        if failed_set:
            print(f"\n❌ 失败 step: {sorted(failed_set)}")
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
