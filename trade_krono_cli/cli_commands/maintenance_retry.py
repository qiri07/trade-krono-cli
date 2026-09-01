"""重跑失败命令 — retry-failed。"""

from __future__ import annotations

import typer
from rich.console import Console

from trade_krono_cli.cli_commands.core import _build_retry_overrides, _load_env

console = Console()


def retry_failed(
    date: str = typer.Option(
        ..., "--date", "-d", help="要重跑的日期 YYYY-MM-DD（默认最新有失败的日期）"
    ),
    module: str = typer.Option(
        None,
        "--module",
        "-m",
        help="指定模块：ta / kronos（None = 全部）",
    ),
    max_retries: int = typer.Option(
        None,
        "--max-retries",
        help="最大重试次数（含首次，默认 3）",
        rich_help_panel="重试策略",
    ),
    base_delay: float = typer.Option(
        None,
        "--base-delay",
        help="基础退避秒数（默认 2.0）",
        rich_help_panel="重试策略",
    ),
    no_jitter: bool = typer.Option(
        False,
        "--no-jitter",
        help="禁用随机抖动",
        rich_help_panel="重试策略",
    ),
) -> None:
    """
    重跑指定日期中失败或不完整的股票。

    行为：
      1. 读取 failure_store.json，筛选出指定日期的失败记录
      2. 若无指定日期，自动使用最新有失败的日期
      3. 仅对失败股票重新执行 TA / Kronos 分析
      4. 更新失败记录（成功则移除，仍失败则更新 attempt_count）

    示例：
      trade-krono-cli retry-failed --date 2026-01-15
      trade-krono-cli retry-failed --module ta --date 2026-01-15
    """
    _load_env()

    from trade_krono_cli.config import reload_settings
    from trade_krono_cli.pipeline import QuantPipeline
    from trade_krono_cli.pipeline_config import PipelineConfig
    from trade_krono_cli.retry_policy import get_failure_store

    store = get_failure_store()
    settings = reload_settings()

    # 若未指定日期，自动使用最新有失败的日期
    if not date or date == "":
        all_fails = store.list_fails()
        if not all_fails:
            console.print("[yellow]⚠️  未发现任何失败记录[/yellow]")
            return
        date = all_fails[0].date
        console.print(f"[dim]未指定日期，自动使用最新失败日期: {date}[/dim]")

    # 筛选失败记录
    fails = store.list_fails(date=date, module=module)
    if not fails:
        console.print(
            f"[yellow]⚠️  日期 {date}{f' (module={module})' if module else ''} 无失败记录[/yellow]"
        )
        return

    failed_tickers = list(dict.fromkeys(r.ticker for r in fails))
    retriable = sum(1 for r in fails if r.error_category == "retriable")
    non_retriable = sum(1 for r in fails if r.error_category == "non_retriable")

    console.print(
        f"[bold green]🔄 重跑失败股票[/bold green] "
        f"date={date} module={module or 'all'} "
        f"({len(failed_tickers)} 只: {retriable} 可重试 / {non_retriable} 不可重试)"
    )

    # 构建重试策略
    retry_overrides = _build_retry_overrides(
        max_retries=max_retries, base_delay=base_delay, no_jitter=no_jitter
    )
    cfg = (
        PipelineConfig.default(settings).override(**retry_overrides)
        if retry_overrides
        else PipelineConfig.default(settings)
    )

    pipeline = QuantPipeline(config=cfg)

    success_count = 0
    still_failed = []
    for i, ticker in enumerate(failed_tickers, 1):
        console.print(f"  [{i}/{len(failed_tickers)}] {ticker} ...", end=" ")
        try:
            if module is None or module == "ta":
                ta_r = pipeline.ta.analyze_one(ticker, date)
                if ta_r.error is None:
                    console.print("[green]✅[/green]")
                else:
                    console.print(f"[red]❌ {ta_r.error[:60]}[/red]")
                    still_failed.append(ticker)
                    continue  # 只重试指定模块时，TA失败即标记为仍失败
            if module is None or module == "kronos":
                if pipeline.kronos is not None:
                    kr = pipeline.kronos.predict_one(ticker, date)
                    if kr.error is None:
                        console.print("[green]✅[/green]")
                    else:
                        console.print(f"[red]❌ {kr.error[:60]}[/red]")
                        still_failed.append(ticker)
                else:
                    console.print("[dim]⊘ (Kronos skipped)[/dim]")
        except Exception as e:
            console.print(f"[red]❌ {type(e).__name__}: {str(e)[:60]}[/red]")
            still_failed.append(ticker)

    success_count = len(failed_tickers) - len(still_failed)

    # 更新失败记录：成功的移除，仍失败的更新 attempt_count
    # 先构建原始记录的 ticker → record 映射（O(n)）
    from trade_krono_cli.retry_policy.store import FailureRecord

    orig_map: dict[str, FailureRecord] = {r.ticker: r for r in fails}
    store.clear_for_date(date, module=module)
    for ticker in still_failed:
        orig = orig_map.get(ticker)
        if orig is not None:
            store.record(
                ticker,
                date,
                orig.module,
                RuntimeError(orig.error_message),
                attempt_count=orig.attempt_count + 1,
            )

    console.print(f"\n[bold]{'✅' if still_failed else '🎉'} 重跑完成[/bold]")
    console.print(f"   本次成功: {success_count}/{len(failed_tickers)}")
    if still_failed:
        console.print(f"   仍失败: {len(still_failed)} 只 → {', '.join(still_failed)}")
    else:
        console.print(f"   已清除日期 {date} 的失败记录（全部成功）")
