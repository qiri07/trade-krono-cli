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
import signal
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts._utils import expected_trading_day  # noqa: E402
from scripts.flow_steps import (  # noqa: E402
    BACKUP_STEP_NAME,
    DRY_RUN,
    MIN_TODAY_KLINE_COUNT,
    STEPS,
    STEPS_WITH_AUTO_BACKUP,
    Step,
    get_step,
)
from trade_krono_cli.config import get_settings  # noqa: E402

# 由 backup step 统一备份后，这些自带备份的 step 追加 --no-backup，保证一轮流程只备一次


def checkpoint_path() -> Path:
    """返回当天 flow checkpoint 文件路径（results 目录，与测试隔离保持一致）。"""
    results_dir = Path(
        os.getenv("TRADING_KRONO_RESULTS_DIR", str(PROJECT_ROOT / "outputs" / "results"))
    )
    return results_dir / f"flow_checkpoint_{expected_trading_day()}.json"


def _get_today_kline_count() -> int:
    """查询今日（期望交易日）A 股（沪+深）的 K 线记录总数。

    用于在执行选股步骤前，校验今日数据量是否达标（>= 5000 条）。
    仅统计 ticker 以 "sh." 或 "sz." 前缀的 A 股记录，不包含港股（hk.）
    和北交所（bj.）。
    若数据库不存在或查询失败，返回 0。
    """
    try:
        settings = get_settings()
        db_path = settings.cache_dir / "pipeline_cache.db"
        if not db_path.exists():
            return 0
        con = sqlite3.connect(str(db_path))
        try:
            today = expected_trading_day()
            cur = con.execute(
                "SELECT COUNT(*) FROM kline_cache "
                "WHERE end=? AND (ticker LIKE 'sh.%' OR ticker LIKE 'sz.%')",
                (today,),
            )
            return cur.fetchone()[0]
        finally:
            con.close()
    except Exception:
        return 0


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
        json.dumps({"date": expected_trading_day(), "steps": statuses}, ensure_ascii=False),
        encoding="utf-8",
    )



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
        # start_new_session：让 step 及其子进程处于独立进程组，超时可整组 kill，
        # 避免只杀掉 uv 而 python/DB 写入孙进程变孤儿继续跑（回归：2026-11-12）。
        proc = subprocess.Popen(
            step.command,
            cwd=str(PROJECT_ROOT),
            shell=False,
            start_new_session=True,
        )
        try:
            rc = proc.wait(timeout=step.timeout)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            print(f"  ⏱️  [{step.name}] 超时（>{step.timeout}s），已终止进程组，按失败处理")
            return 124
        except OSError:
            return 127
    except OSError as e:
        print(f"  ✗ [{step.name}] 启动失败: {e}")
        return 127
    if rc != 0:
        print(f"  ✗ [{step.name}] 失败（exit {rc}）")
    return rc


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
    force: bool = False,
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
        # 本轮启用 step 的 name 集合，用于过滤 checkpoint 中不属于本轮的残留状态
        enabled_names: set[str] = {s.name for s in targets}
        statuses: dict[str, str] = {}
        # 跨轮脏读修复：非 resume 模式下，清掉 checkpoint 里不属于本轮 targets 的 step
        # 状态（例如上一轮启用了 sync_hk 本轮 --disable 了它，残留的 failed 会误导）。
        if not resume:
            legacy = load_checkpoint()
            for name in enabled_names:
                if name in legacy:
                    statuses[name] = legacy[name]
            # 丢弃本轮未启用的 step 残留
            stale = [n for n in legacy if n not in enabled_names]
            if stale:
                print(f"  🧹 已清除 checkpoint 中本轮未启用的 step 残留: {sorted(stale)}")
        # 本轮是否已有统一备份：backup step 在本次执行计划内，或当天 checkpoint 已记录成功
        backup_done = any(s.name == BACKUP_STEP_NAME for s in targets)
        if resume:
            # resume 保留 checkpoint 全部累积状态（含未启用的 step），
            # 用于 backup 成功判断与断点跳过；不做残留清理。
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
        skipped_set: set[str] = set()
        first_failure_rc: int | None = None
        # 今日数据量门控：仅在首次遇到选择层前检查一次，避免重复判定
        selection_gate_checked = False
        selection_gate_blocked = False
        SELECTION_STEP_NAMES = frozenset(
            {
                "buffett",
                "resonance",
                "multi_head",
                "whitelist",
                "double_ma",
                "macd_ma",
                "czsc",
                "intraday",
                "sixdim",
                "combined",
            }
        )
        for layer_idx, layer in enumerate(layers, 1):
            # 检查今日数据量门控（仅在首次遇到选择层前检查一次，避免每层重复判定）
            has_selection = any(s.name in SELECTION_STEP_NAMES for s in layer)
            if has_selection and not selection_gate_checked:
                selection_gate_checked = True
                today_count = _get_today_kline_count()
                if today_count < MIN_TODAY_KLINE_COUNT and not force:
                    print(
                        f"  ⚠️  今日数据量 {today_count} < {MIN_TODAY_KLINE_COUNT} 条，跳过选股步骤（--force 强制执行）"
                    )
                    selection_gate_blocked = True

            if selection_gate_blocked:
                # 跳过本层的所有选择步骤，不污染 failed_set
                selection_in_layer = [s for s in layer if s.name in SELECTION_STEP_NAMES]
                for s in selection_in_layer:
                    print(
                        f"  ⏭️  跳过 [{s.name}]：今日数据量不足，未达到 {MIN_TODAY_KLINE_COUNT} 条"
                    )
                    statuses[s.name] = "skipped"
                    skipped_set.add(s.name)
                runnable = [s for s in layer if s.name not in SELECTION_STEP_NAMES]
                if not runnable:
                    continue
            else:
                # 过滤掉依赖已失败或已跳过 step 的 step（避免基于缺失/脏数据推导后续结论）
                runnable = [
                    s
                    for s in layer
                    if not any(
                        d in failed_set or d in skipped_set for d in s.depends_on
                    )
                ]
                skipped_in_layer = [s for s in layer if s not in runnable]
                for s in skipped_in_layer:
                    print(
                        f"  ⏭️  跳过 [{s.name}]：前置 step 不可用（{sorted(d for d in s.depends_on if d in failed_set or d in skipped_set)}）"
                    )
                    statuses[s.name] = "skipped"
                    skipped_set.add(s.name)
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
        # 单步执行只覆盖当前 step 状态，保留当天已累积的其他 step 记录。
        # 跨轮脏读修复：单步 checkpoint 无法知道"本轮启用集合"，但可确保
        # 当前 step 本身的状态被更新；其他 step 状态沿用（属于合理累积）。
        statuses = load_checkpoint()
        statuses[step] = "success" if rc == 0 else "failed"
        save_checkpoint(statuses)
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
    p_run.add_argument(
        "--enable",
        action="append",
        default=[],
        help="强制启用默认关闭 step（可重复或逗号分隔，两种写法均可）",
    )
    p_run.add_argument(
        "--disable",
        action="append",
        default=[],
        help="强制排除 step（可重复或逗号分隔，两种写法均可）",
    )
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
    p_run.add_argument(
        "--force",
        action="store_true",
        help="强制执行所有步骤（即使今日增量数据 < 5000 也执行选股步骤）",
    )

    def _run(args: argparse.Namespace) -> int:
        # --enable/--disable 支持重复传参（append），也支持逗号分隔；统一摊平。
        enable_list = [x for v in args.enable for x in v.split(",") if x]
        disable_list = [x for v in args.disable for x in v.split(",") if x]
        return cmd_run(
            step=args.step,
            all_steps=args.all,
            enable=enable_list,
            disable=disable_list,
            dry_run=args.dry_run,
            strict=args.strict,
            resume=args.resume,
            force=args.force,
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
