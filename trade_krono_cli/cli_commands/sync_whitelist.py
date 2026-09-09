"""sync-whitelist 命令 — 白名单股票 K 线缓存同步。"""

from __future__ import annotations

from datetime import datetime

import typer
from rich.console import Console

from trade_krono_cli.cli_commands._sync_helpers import (
    _resolve_tickers,
    _run_sync,
)
from trade_krono_cli.cli_commands.core import _load_env

console = Console()


def sync_whitelist(
    date: str = typer.Option(
        datetime.now().strftime("%Y-%m-%d"),
        "--date",
        "-d",
        help="基准日期 YYYY-MM-DD（默认今天）",
    ),
    lookback: int = typer.Option(730, "--lookback", "-l", help="回溯天数，默认 730（约 2 年）"),
    workers: int = typer.Option(
        16,
        "--workers",
        "-w",
        help="并发拉取线程数（默认 16）",
    ),
    show_progress: bool = typer.Option(
        True,
        "--no-progress",
        "-p",
        help="不显示进度条（静默模式）",
    ),
) -> None:
    """仅同步白名单股票的 K 线缓存。

    白名单来自 .env 中的 SYNC_WHITELIST 配置（逗号分隔的6位股票代码）。
    若未配置 SYNC_WHITELIST，命令将报错退出。

    示例：
      trade-krono-cli sync-whitelist                    # 同步 .env 中配置的白名单
      trade-krono-cli sync-whitelist --lookback 1095    # 同步 3 年历史
      trade-krono-cli sync-whitelist --date 2026-08-30  # 指定基准日期
    """
    _load_env()

    from trade_krono_cli.config import get_settings

    settings = get_settings()
    whitelist_raw = settings.sync_whitelist.strip()
    if not whitelist_raw:
        console.print("[red]❌ 未配置 SYNC_WHITELIST，请在 .env 中设置白名单股票代码[/red]")
        raise typer.Exit(1)

    whitelist_tickers = _resolve_tickers(whitelist_raw)
    if not whitelist_tickers:
        console.print("[red]❌ 白名单解析后无有效股票，请检查 SYNC_WHITELIST 格式[/red]")
        raise typer.Exit(1)

    # ── 运行同步 ───────────────────────────────────────────────────────────────
    _run_sync(
        tickers=whitelist_tickers,
        date=date,
        lookback=lookback,
        workers=workers,
        show_progress=show_progress,
        label="白名单",
    )
