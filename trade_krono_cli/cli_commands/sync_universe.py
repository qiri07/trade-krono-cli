"""sync-universe 命令 — 全量 A 股 K 线缓存同步。"""

from __future__ import annotations

from datetime import datetime

import typer
from loguru import logger
from rich.console import Console

from trade_krono_cli.cli_commands._sync_helpers import (
    _resolve_tickers,
    _run_sync,
)
from trade_krono_cli.cli_commands.core import _load_env

console = Console()


def sync_universe(
    source: str = typer.Option(
        "tonghuashun",
        "--source",
        "-s",
        help="股票池来源：tonghuashun / mootdx / akshare（默认 tonghuashun）",
    ),
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
    """全量 A 股 K 线缓存同步。

    从指定 UniverseProvider 获取全市场 A 股列表，逐只拉取历史 K 线并写入缓存。
    首次运行拉取全量历史（全部永久缓存）；
    后续运行自动增量更新，仅拉取新增交易日数据。

    若配置了 SYNC_WHITELIST，白名单股票将优先拉取，随后再处理全量列表（白名单股票已从全量中剔除，避免重复）。

    示例：
      trade-krono-cli sync-universe                  # 用同花顺同步全部 A 股
      trade-krono-cli sync-universe --source mootdx  # 用 mootdx 同步
      trade-krono-cli sync-universe --lookback 1095  # 同步 3 年历史
    """
    _load_env()

    from trade_krono_cli.config import get_settings

    settings = get_settings()
    whitelist_tickers: list[str] = _resolve_tickers(settings.sync_whitelist)

    # ── 获取 A 股列表 ──────────────────────────────────────────────────────────
    if source == "tonghuashun":
        from trade_krono_cli.universe.provider import TongHuaShunUniverseProvider

        provider = TongHuaShunUniverseProvider()
        tickets = provider.get_universe()
        tickers = [t.ticker for t in tickets if t.ticker]
        logger.info(f"📋 同花顺 A 股列表: {len(tickers)} 只")
    else:
        from trade_krono_cli.universe.provider import get_universe_provider

        provider_other = get_universe_provider(source)
        if provider_other is None:
            console.print(f"[red]❌ 数据源 '{source}' 不可用，请检查配置[/red]")
            raise typer.Exit(1)
        tickets = provider_other.get_universe()
        tickers = [t.ticker for t in tickets if t.ticker]
        logger.info(f"📋 {source} A 股列表: {len(tickers)} 只")

    if not tickers:
        console.print("[red]❌ 无法获取 A 股列表，请检查数据源配置[/red]")
        raise typer.Exit(1)

    # ── 白名单优先：从全量中剔除白名单股票，避免重复拉取 ──────────────────────
    whitelist_set = set(whitelist_tickers)
    remaining_tickers = [t for t in tickers if t not in whitelist_set]
    ordered_tickers = whitelist_tickers + remaining_tickers
    if whitelist_tickers:
        logger.info(f"📌 白名单 {len(whitelist_tickers)} 只优先拉取")

    # ── 运行同步 ───────────────────────────────────────────────────────────────
    _run_sync(
        tickers=ordered_tickers,
        date=date,
        lookback=lookback,
        workers=workers,
        show_progress=show_progress,
        label="全量A股",
    )
