#!/usr/bin/env python3
"""统一投研流程编排入口。

把「每日流程 / 六维选股 / 两个结果的交集」三个脚本整理为一个方便调用的
CLI，每个子命令对应一个独立流程，参数透传、退出码透传，不修改任何
现有脚本：

  uv run python scripts/pipeline_entry.py daily  [env 标志]          # 每日全流程（daily_flow.sh）
  uv run python scripts/pipeline_entry.py sixdim [六维参数]           # 六维选股（six_dim_screen.py）
  uv run python scripts/pipeline_entry.py cross  [交集参数]           # 流程交集 ∩ 六维选股（cross_screen_report.py）

三个子命令共用：
  --date YYYY-MM-DD   目标日期（缺省：daily 用今日；六维/交集用最近交易日）
  --no-push           跳过飞书推送

调用示例：
  # 1) 跑完今日全流程（同步→巴菲特→共振→白名单→双均线→macd_ma→czsc→intraday→combined 交集）
  uv run python scripts/pipeline_entry.py daily

  # 2) 仅跑六维选股，推荐前 20 只，不推送飞书
  uv run python scripts/pipeline_entry.py sixdim --date 2026-11-11 --top 20 --no-push

  # 3) 仅算“每日流程结果 ∩ 六维选股”的交集，卡片落盘不推送
  uv run python scripts/pipeline_entry.py cross --date 2026-11-11 --no-push

  # 4) 全流程跑完后再补跑六维 + 交集
  uv run python scripts/pipeline_entry.py daily --no-push
  uv run python scripts/pipeline_entry.py sixdim --push 1
  uv run python scripts/pipeline_entry.py cross --push 1
"""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run(command: list[str], env: dict[str, str] | None = None) -> int:
    """在仓库根目录下执行命令，原样打印输出并返回退出码。"""
    print(f"\n$ {' '.join(command)}")
    result = subprocess.run(command, cwd=str(REPO_ROOT), env=env or os.environ.copy())
    return result.returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="统一投研流程编排入口（每日流程 / 六维选股 / 流程∩六维交集）"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # ──────────────────────────────────────────────────────────────────
    # daily：每日全流程
    # ──────────────────────────────────────────────────────────────────
    p_daily = sub.add_parser("daily", help="执行每日投研全流程（daily_flow.sh / flow_runner.py）")
    p_daily.add_argument("--date", default=None, help="目标日期 YYYY-MM-DD（缺省今日）")
    p_daily.add_argument("--no-push", action="store_true", help="跳过飞书推送（NO_PUSH=1）")
    p_daily.add_argument("--no-sync", action="store_true", help="跳过数据同步类 step（NO_SYNC=1）")
    p_daily.add_argument("--skip-etf", action="store_true", help="跳过 ETF 同步（SKIP_ETF=1）")
    p_daily.add_argument("--export", action="store_true", help="额外执行共享数据导出（EXPORT=1）")
    p_daily.add_argument("--resume", action="store_true", help="断点续传（RESUME=1）")
    p_daily.add_argument("--force", action="store_true", help="强制执行所有步骤（FORCE=1）")
    p_daily.add_argument("--dry-run", action="store_true", help="只打印不执行（DRY_RUN=1）")
    p_daily.set_defaults(func=_cmd_daily)

    # ──────────────────────────────────────────────────────────────────
    # sixdim：六维选股
    # ──────────────────────────────────────────────────────────────────
    p_six = sub.add_parser("sixdim", help="执行六维选股（scripts.six_dim_screen.py）")
    p_six.add_argument("--date", default=None, help="分析日期 YYYY-MM-DD（缺省今日）")
    p_six.add_argument("--top", type=int, default=10, help="推荐股票数量（默认 10）")
    p_six.add_argument(
        "--min-cap", type=float, default=None, help="最小总市值（亿元，缺省脚本默认 50）"
    )
    p_six.add_argument("--no-fetch", action="store_true", help="仅用本地缓存，跳过联网接口")
    p_six.add_argument(
        "--max-candidates", type=int, default=None, help="进入技术面计算的最大候选数"
    )
    p_six.add_argument(
        "--push", type=int, choices=[0, 1], default=None, help="是否飞书推送（1=是，0=否）"
    )
    p_six.add_argument("--no-push", action="store_true", help="跳过飞书推送（等价 --push 0）")
    p_six.set_defaults(func=_cmd_sixdim)

    # ──────────────────────────────────────────────────────────────────
    # cross：流程交集 ∩ 六维选股
    # ──────────────────────────────────────────────────────────────────
    p_cross = sub.add_parser(
        "cross", help="计算“每日流程结果 ∩ 六维选股”的交集（cross_screen_report.py）"
    )
    p_cross.add_argument("--date", default=None, help="目标日期 YYYY-MM-DD（缺省最近交易日）")
    p_cross.add_argument(
        "--min-sources", type=int, default=3, help="流程交集最少命中选股线条数（默认 3）"
    )
    p_cross.add_argument("--no-push", action="store_true", help="只生成卡片文件，不推送飞书")
    p_cross.add_argument(
        "--out",
        default=None,
        help="卡片文本输出路径（缺省 outputs/results/cross_flow_sixdim_<YYYYMMDD>.txt）",
    )
    p_cross.set_defaults(func=_cmd_cross)

    return parser


# ──────────────────────────────────────────────────────────────────────
# 子命令实现
# ──────────────────────────────────────────────────────────────────────
def _cmd_daily(args: argparse.Namespace) -> int:
    env = os.environ.copy()
    if args.no_push:
        env["NO_PUSH"] = "1"
    if args.no_sync:
        env["NO_SYNC"] = "1"
    if args.skip_etf:
        env["SKIP_ETF"] = "1"
    if args.export:
        env["EXPORT"] = "1"
    if args.resume:
        env["RESUME"] = "1"
    if args.force:
        env["FORCE"] = "1"
    if args.dry_run:
        env["DRY_RUN"] = "1"

    # daily_flow.sh 本身不接受 --date，日期由脚本内部期望交易日/流程决定；
    # 若显式传了 --date，提示用户（兼容性提醒，不阻断）。
    if args.date is not None:
        print(
            f"[提示] daily 子命令不绑定 --date，日期由 daily_flow.sh/flow_runner.py 内部决定；"
            f"已收到 --date {args.date} 但不透传。"
        )

    return _run(["bash", "scripts/daily_flow.sh"], env=env)


def _cmd_sixdim(args: argparse.Namespace) -> int:
    cmd = [
        "uv",
        "run",
        "python",
        "scripts/six_dim_screen.py",
        "--top",
        str(args.top),
    ]
    if args.date is not None:
        cmd += ["--date", args.date]
    if args.min_cap is not None:
        cmd += ["--min-cap", str(args.min_cap)]
    if args.no_fetch:
        cmd.append("--no-fetch")
    if args.max_candidates is not None:
        cmd += ["--max-candidates", str(args.max_candidates)]
    if args.no_push:
        cmd += ["--push", "0"]
    elif args.push is not None:
        cmd += ["--push", str(args.push)]
    return _run(cmd)


def _cmd_cross(args: argparse.Namespace) -> int:
    cmd = [
        "uv",
        "run",
        "python",
        "scripts/cross_screen_report.py",
        "--min-sources",
        str(args.min_sources),
    ]
    if args.date is not None:
        cmd += ["--date", args.date]
    if args.no_push:
        cmd.append("--no-push")
    if args.out is not None:
        cmd += ["--out", args.out]
    return _run(cmd)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
