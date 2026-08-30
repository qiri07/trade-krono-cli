"""
trade-krono-cli CLI 入口 — Typer 实现。

支持的命令：
  run           一键运行（TA + Kronos 并行）
  ta            仅 TradingAgents 分析
  kronos        仅 Kronos 预测
  status        查看系统状态
  history       查看历史分析记录
  repo          外部项目管理（status / doctor / update / pin）
  clear-cache   清除缓存
  eval-prediction 预测评估

命令实现详见 cli_commands.py。
"""

from __future__ import annotations

import typer
from rich.console import Console

app = typer.Typer(
    name="trade-krono-cli",
    help="🏭 A股投研+预测一体化流水线 (TradingAgents + Kronos 并行)",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()

# ═══════════════════════════════════════════════════════
# repo — 外部项目管理子命令组
# ═══════════════════════════════════════════════════════

repo_app = typer.Typer(
    help="📦 外部项目管理：TradingAgents-astock、Kronos 等下游依赖",
)
app.add_typer(repo_app, name="repo")

# ═══════════════════════════════════════════════════════
# 工具函数（由 cli_commands 模块提供，此处通过装饰器注册）
# ═══════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════
# 注册命令（显式装饰，避免模块级装饰器的循环导入问题）
# ═══════════════════════════════════════════════════════
from trade_krono_cli.cli_commands import (  # noqa: E402
    clear_cache,
    eval_prediction,
    history,
    kronos,
    repo_doctor,
    repo_pin,
    repo_status,
    repo_update,
    retry_failed,
    run,
    status,
    sync_universe,
    ta,
    warm_cache,
)

repo_app.command()(repo_status)
repo_app.command()(repo_doctor)
repo_app.command()(repo_update)
repo_app.command()(repo_pin)

app.command()(run)
app.command()(ta)
app.command()(kronos)
app.command()(status)
app.command()(clear_cache)
app.command()(warm_cache)
app.command()(sync_universe)
app.command()(history)
app.command()(eval_prediction)
app.command()(retry_failed)


# ═══════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════


def main() -> None:
    app()


if __name__ == "__main__":
    main()
