"""缓存管理命令 — clear-cache / warm-cache。"""

from __future__ import annotations

import typer
from rich.console import Console

from trade_krono_cli.cli_commands.core import _load_env, _load_tickers

console = Console()


def clear_cache() -> None:
    """清除所有缓存（K线/TA/Kronos），不影响研究数据库。"""
    _load_env()

    from trade_krono_cli.cache import get_cache

    n = get_cache().clear_all()
    console.print(f"[yellow]🧹 已清除 {n} 条缓存[/yellow]")


def warm_cache(
    tickers: str | None = typer.Option(
        None, "--tickers", "-t", help="逗号分隔的股票代码，如 600519,000858,600036"
    ),
    config: str | None = typer.Option(
        None, "--config", "-c", help="股票列表文件路径（每行一只，支持 # 注释）"
    ),
    date: str = typer.Option(..., "--date", "-d", help="基准日期 YYYY-MM-DD（默认今天）"),
    lookback: int = typer.Option(730, "--lookback", "-l", help="回溯天数，默认 730（2年）"),
) -> None:
    """盘前缓存预热：批量拉取 K 线数据并写入缓存（全部永久）。
    可显著减少盘中运行的首次数据拉取耗时。
    """
    _load_env()

    from trade_krono_cli.cache import get_cache

    tk_list = _load_tickers(tickers, config)
    if not tk_list:
        console.print("[red]❌ 股票列表为空（请通过 --tickers 或 --config 提供）[/red]")
        raise typer.Exit(1)

    cache = get_cache()
    total_rows, total_segments = 0, 0
    console.print(
        f"[bold green]🔥 缓存预热[/bold green] {len(tk_list)} 只 → {date} (回溯 {lookback} 天)"
    )
    for i, tk in enumerate(tk_list, 1):
        console.print(f"  [{i}/{len(tk_list)}] {tk} ...", end="")
        rows, segs = cache.warm_history(tk, date, lookback_days=lookback)
        total_rows += rows
        total_segments += segs
        console.print(f" ✅ {rows}行/{segs}段")

    console.print(
        f"[bold green]✅ 预热完成[/bold green] 共 {total_rows} 行 / {total_segments} 个缓存段"
    )
