"""同步命令 — sync-universe / sync-whitelist。"""

from __future__ import annotations

import time
from datetime import datetime, timedelta

import typer
from loguru import logger
from rich.console import Console

from trade_krono_cli.cli_commands.core import _load_env

console = Console()

_EXCHANGE_PREFIX: dict[str, str] = {
    "6": "sh.",  # 上交所主板 + 科创板
    "0": "sz.",  # 深交所主板
    "3": "sz.",  # 创业板
    "9": "bj.",  # 北交所
}


def _resolve_tickers(raw: str) -> list[str]:
    """将逗号分隔的6位股票代码转为带交易所前缀的 ticker 列表。

    规则：6xxxxx→sh.，0/3xxxxx→sz.，9xxxxx→bj.。
    自动去重并保持首次出现的顺序。
    """
    seen: set[str] = set()
    result: list[str] = []
    if not raw:
        return result
    for code in (c.strip() for c in raw.split(",") if c.strip()):
        if len(code) != 6 or not code.isdigit():
            logger.warning(f"⚠️  白名单代码格式错误，已跳过: {code}")
            continue
        prefix = _EXCHANGE_PREFIX.get(code[0])
        if prefix is None:
            logger.warning(f"⚠️  未知交易所前缀，已跳过: {code}")
            continue
        ticker = f"{prefix}{code}"
        if ticker not in seen:
            seen.add(ticker)
            result.append(ticker)
    return result


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
    delay: float = typer.Option(
        0.05, "--delay", help="每只股票之间的延迟秒数（默认 0.05，用于限流保护）",
    ),
    show_progress: bool = typer.Option(
        True, "--no-progress", "-p", help="不显示进度条（静默模式）",
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
    from trade_krono_cli.data import fetch_kline_incremental

    settings = get_settings()
    whitelist_tickers: list[str] = _resolve_tickers(settings.sync_whitelist)

    end_date = datetime.strptime(date, "%Y-%m-%d")
    start_date = (end_date - timedelta(days=lookback * 2)).strftime("%Y-%m-%d")

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

    total = len(ordered_tickers)
    success_count = 0
    fail_tickers: list[str] = []

    console.print(
        f"[bold green]🔥 全量 K 线缓存同步[/bold green] "
        f"来源={source} 股票数={total} 日期={start_date}~{date}"
        + (f" 白名单={len(whitelist_tickers)}只优先" if whitelist_tickers else ""),
    )

    for i, ticker in enumerate(ordered_tickers, 1):
        if show_progress:
            console.print(f"  [{i}/{total}] {ticker} ...", end="\r")

        try:
            df = fetch_kline_incremental(
                ticker=ticker,
                start_date=start_date,
                end_date=date,
                frequency="d",
                adjustflag="1",
                use_cache=True,
            )
            n_rows = len(df) if df is not None else 0
            success_count += 1
            if show_progress:
                console.print(f"  [{i}/{total}] {ticker} ✅ {n_rows}行", end="\r")
        except Exception as e:
            fail_tickers.append(ticker)
            logger.debug(f"⚠️  {ticker} K 线拉取失败: {e}")
            if show_progress:
                console.print(f"  [{i}/{total}] {ticker} ❌ {str(e)[:40]}", end="\r")

        if delay > 0 and i < total:
            time.sleep(delay)

    if show_progress:
        console.print()  # 换行，清除进度行

    console.print(
        f"[bold green]✅ 同步完成[/bold green] "
        f"成功={success_count}/{total}  失败={len(fail_tickers)}",
    )
    if fail_tickers:
        console.print(f"[yellow]⚠️  失败股票（可稍后重试）: {', '.join(fail_tickers[:20])}[/yellow]")
        if len(fail_tickers) > 20:
            console.print(f"[dim]   … 还有 {len(fail_tickers) - 20} 只[/dim]")

    # ── 自动导出 daily_pv 供 RD-Agent 使用 ─────────────────────────────
    try:
        from pathlib import Path as _P

        from trade_krono_cli.cache import get_cache

        cache = get_cache()
        rdagent_data = _P(__file__).resolve().parents[3] / "RD-Agent-Work" / "git_ignore_folder"
        main_dir = rdagent_data / "factor_implementation_source_data"
        parquet_main = main_dir / "daily_pv.parquet"
        h5_main = main_dir / "daily_pv.h5"

        result = cache.export_daily_pv(
            parquet_path=str(parquet_main),
            h5_path=str(h5_main),
            debug_insts=100,
        )
        console.print(f"[bold green]✅ 已自动导出 daily_pv: {result['stocks']:,} 只, "
                      f"{result['rows']:,} 行 ({result['date_min']} ~ {result['date_max']})[/bold green]")
    except Exception as ex:
        logger.warning(f"自动导出 daily_pv 失败（不影响同步结果）: {ex}")


def sync_whitelist(
    date: str = typer.Option(
        datetime.now().strftime("%Y-%m-%d"),
        "--date",
        "-d",
        help="基准日期 YYYY-MM-DD（默认今天）",
    ),
    lookback: int = typer.Option(730, "--lookback", "-l", help="回溯天数，默认 730（约 2 年）"),
    delay: float = typer.Option(
        0.05, "--delay", help="每只股票之间的延迟秒数（默认 0.05，用于限流保护）",
    ),
    show_progress: bool = typer.Option(
        True, "--no-progress", "-p", help="不显示进度条（静默模式）",
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
    from trade_krono_cli.data import fetch_kline_incremental

    settings = get_settings()
    whitelist_raw = settings.sync_whitelist.strip()
    if not whitelist_raw:
        console.print("[red]❌ 未配置 SYNC_WHITELIST，请在 .env 中设置白名单股票代码[/red]")
        raise typer.Exit(1)

    whitelist_tickers = _resolve_tickers(whitelist_raw)
    if not whitelist_tickers:
        console.print("[red]❌ 白名单解析后无有效股票，请检查 SYNC_WHITELIST 格式[/red]")
        raise typer.Exit(1)

    end_date = datetime.strptime(date, "%Y-%m-%d")
    start_date = (end_date - timedelta(days=lookback * 2)).strftime("%Y-%m-%d")

    total = len(whitelist_tickers)
    success_count = 0
    fail_tickers: list[str] = []

    console.print(
        f"[bold green]🔥 白名单 K 线缓存同步[/bold green] 股票数={total} 日期={start_date}~{date}",
    )

    for i, ticker in enumerate(whitelist_tickers, 1):
        if show_progress:
            console.print(f"  [{i}/{total}] {ticker} ...", end="\r")

        try:
            df = fetch_kline_incremental(
                ticker=ticker,
                start_date=start_date,
                end_date=date,
                frequency="d",
                adjustflag="1",
                use_cache=True,
            )
            n_rows = len(df) if df is not None else 0
            success_count += 1
            if show_progress:
                console.print(f"  [{i}/{total}] {ticker} ✅ {n_rows}行", end="\r")
        except Exception as e:
            fail_tickers.append(ticker)
            logger.debug(f"⚠️  {ticker} K 线拉取失败: {e}")
            if show_progress:
                console.print(f"  [{i}/{total}] {ticker} ❌ {str(e)[:40]}", end="\r")

        if delay > 0 and i < total:
            time.sleep(delay)

    if show_progress:
        console.print()

    console.print(
        f"[bold green]✅ 同步完成[/bold green] "
        f"成功={success_count}/{total}  失败={len(fail_tickers)}",
    )
    if fail_tickers:
        console.print(f"[yellow]⚠️  失败股票: {', '.join(fail_tickers)}[/yellow]")


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
