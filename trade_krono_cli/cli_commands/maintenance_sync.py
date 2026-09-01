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
        0.05, "--delay", help="每只股票之间的延迟秒数（默认 0.05，用于限流保护）"
    ),
    show_progress: bool = typer.Option(
        True, "--no-progress", "-p", help="不显示进度条（静默模式）"
    ),
) -> None:
    """
    全量 A 股 K 线缓存同步。

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
        + (f" 白名单={len(whitelist_tickers)}只优先" if whitelist_tickers else "")
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
        f"成功={success_count}/{total}  失败={len(fail_tickers)}"
    )
    if fail_tickers:
        console.print(f"[yellow]⚠️  失败股票（可稍后重试）: {', '.join(fail_tickers[:20])}[/yellow]")
        if len(fail_tickers) > 20:
            console.print(f"[dim]   … 还有 {len(fail_tickers) - 20} 只[/dim]")


def sync_whitelist(
    date: str = typer.Option(
        datetime.now().strftime("%Y-%m-%d"),
        "--date",
        "-d",
        help="基准日期 YYYY-MM-DD（默认今天）",
    ),
    lookback: int = typer.Option(730, "--lookback", "-l", help="回溯天数，默认 730（约 2 年）"),
    delay: float = typer.Option(
        0.05, "--delay", help="每只股票之间的延迟秒数（默认 0.05，用于限流保护）"
    ),
    show_progress: bool = typer.Option(
        True, "--no-progress", "-p", help="不显示进度条（静默模式）"
    ),
) -> None:
    """
    仅同步白名单股票的 K 线缓存。

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
        f"[bold green]🔥 白名单 K 线缓存同步[/bold green] 股票数={total} 日期={start_date}~{date}"
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
        f"成功={success_count}/{total}  失败={len(fail_tickers)}"
    )
    if fail_tickers:
        console.print(f"[yellow]⚠️  失败股票: {', '.join(fail_tickers)}[/yellow]")
