"""trade-krono-cli CLI 入口 — Typer 实现。

支持的命令：
  run           一键运行（TA + Kronos 并行）
  ta            仅 TradingAgents 分析
  kronos        仅 Kronos 预测
  status        查看系统状态
  history       查看历史分析记录
  repo          外部项目管理（status / doctor / update / pin）
  clear-cache   清除缓存
  eval-prediction 预测评估

信号处理：
  - SIGTERM / SIGINT：优雅退出，清理资源
"""

from __future__ import annotations

import signal
import sys

import typer
from loguru import logger
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
    export_daily_pv,
    history,
    kronos,
    rank_providers,
    repo_doctor,
    repo_pin,
    repo_status,
    repo_update,
    retry_failed,
    run,
    status,
    sync_universe,
    sync_whitelist,
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
app.command()(sync_whitelist)
app.command()(history)
app.command()(eval_prediction)
app.command()(retry_failed)
app.command()(rank_providers)
app.command()(export_daily_pv)


# ═══════════════════════════════════════════════════════
# 信号处理 — 优雅退出
# ═══════════════════════════════════════════════════════


def _graceful_shutdown(signum: int, frame: object) -> None:
    """收到终止信号时优雅退出。"""
    sig_name = signal.Signals(signum).name
    logger.info(f"收到信号 {sig_name}，正在优雅退出...")
    # 清理资源：关闭日志、数据库连接等
    try:
        from trade_krono_cli.data_providers.factory import reset_data_factory

        reset_data_factory()
    except Exception:
        pass
    sys.exit(0)


# 注册信号处理器
signal.signal(signal.SIGTERM, _graceful_shutdown)
signal.signal(signal.SIGINT, _graceful_shutdown)


# ═══════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════


def main() -> None:
    app()


if __name__ == "__main__":
    main()
