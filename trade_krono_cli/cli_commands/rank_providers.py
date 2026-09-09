"""rank-providers 命令 — Benchmark 所有数据源延迟。"""

from __future__ import annotations

import typer
from rich.console import Console

from trade_krono_cli.cli_commands.core import _load_env

console = Console()


def rank_providers(
    ticker: str = typer.Option(
        "sh.600519",
        "--ticker",
        "-t",
        help="用于 benchmark 的代表性 ticker（默认 sh.600519 贵州茅台）",
    ),
    workers: int = typer.Option(3, "--workers", "-w", help="并发 benchmark 线程数（默认 3）"),
    force: bool = typer.Option(False, "--force", "-f", help="强制重新 benchmark，忽略缓存"),
) -> None:
    """Benchmark 所有数据源延迟，按速度排序输出。

    结果会缓存 10 分钟，后续 K 线拉取自动按此顺序尝试。
    北交所（bj.）ticker 会自动将 tonghuashun 置顶。

    示例：
      trade-krono-cli rank-providers                    # 用默认 ticker benchmark
      trade-krono-cli rank-providers -t bj.920001       # 测试北交所
      trade-krono-cli rank-providers --force            # 强制重新 benchmark
    """
    _load_env()

    from trade_krono_cli.data_providers.factory import get_data_factory

    factory = get_data_factory()
    ticker_type = ticker.split(".", maxsplit=1)[0] if "." in ticker else ticker

    console.print(f"[bold cyan]🔬 Provider Benchmark[/bold cyan] ticker={ticker} workers={workers}")
    console.print()

    if force:
        # 清除该 ticker 类型的缓存，强制重新 benchmark
        factory.invalidate_rank_cache(ticker_type)

    results = factory.bench_all(ticker=ticker, workers=workers)

    if not results:
        console.print("[red]❌ 没有可用的 Provider[/red]")
        raise typer.Exit(1)

    console.print()
    console.print("[bold]结果排名（越快越靠前）:[/bold]")
    console.print(f"  {'排名':<4s} {'Provider':<14s} {'延迟':<10s} {'状态':<6s}")
    console.print("  " + "-" * 38)

    for i, r in enumerate(results, 1):
        if r.success:
            latency_str = f"{r.latency_ms:.0f}ms"
            status_str = "✅"
        else:
            latency_str = "FAIL"
            status_str = "❌"
        console.print(f"  {i:<4d} {r.name:<14s} {latency_str:<10s} {status_str:<6s}")

    # 写入缓存，避免重复 benchmark
    ranked = factory.get_ranked_chain_for_ticker(ticker)
    console.print()
    console.print(f"[bold green]✅ 已缓存 Provider 排序[/bold green] {' → '.join(ranked)}")
    console.print("  缓存 TTL: 10 分钟，下次 benchmark 将在 10 分钟后生效")
