"""
CLI 维护命令 — status / clear-cache / warm-cache / sync-universe / history / eval-prediction / retry-failed。
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta

import typer
from loguru import logger
from rich.console import Console
from rich.table import Table

from trade_krono_cli.cli_commands.core import _build_retry_overrides, _load_env, _load_tickers

console: Console = Console()


# ═══════════════════════════════════════════════════════
# 白名单辅助函数
# ═══════════════════════════════════════════════════════

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



# ═══════════════════════════════════════════════════════
# status — 查看系统状态
# ═══════════════════════════════════════════════════════


def status() -> None:
    """查看系统状态：密钥、缓存、模型配置 + 健康检查。"""
    s, _ = _load_env()

    from trade_krono_cli.cache import get_cache
    from trade_krono_cli.security import KeyVault

    vault = KeyVault()
    status_map = vault.validate()

    table = Table(title="🔐 系统状态", header_style="bold cyan")
    for col in ("项目", "状态"):
        table.add_column(col)
    table.add_row("项目根目录", str(s.project_root))
    table.add_row("结果目录", str(s.results_dir))
    table.add_row("缓存目录", str(s.cache_dir))
    table.add_row("LLM 供应商", s.llm_provider)
    table.add_row("Deep 模型", s.deep_think_llm)
    table.add_row("Quick 模型", s.quick_think_llm)
    table.add_row("Kronos 模型", s.kronos_model)
    table.add_row("Kronos 设备", s.kronos_device)
    for k, v in status_map.items():
        table.add_row(k, "✅ 已配置" if v else "⚠️ 缺失")
    console.print(table)

    try:
        cache_stats = get_cache().stats()
        console.print(f"[dim]缓存: {cache_stats}[/dim]")
    except Exception as e:
        console.print(f"[dim]缓存统计不可用: {e}[/dim]")

    # ── 健康检查 ────────────────────────────────────────────────────────────
    from trade_krono_cli.health import health_summary, print_health_report

    results = health_summary(s)
    print_health_report(results)

    try:
        from trade_krono_cli.research_db import get_research

        res_stats = get_research().stats()
        console.print(f"[dim]研究数据库: {res_stats}[/dim]")
        jobs = get_research().list_jobs(limit=5)
        if jobs:
            console.print("[bold]最近分析作业:[/bold]")
            for j in jobs:
                run_id_str = f" run={j.get('run_id', '-')}" if j.get("run_id") else ""
                dv_str = f" data={j.get('data_version', '-')}" if j.get("data_version") else ""
                ch_str = f" hash={j.get('config_hash', '-')[:8]}…" if j.get("config_hash") else ""
                console.print(
                    f"  • [{j['date']}] job={j['job_id']}"
                    f"{run_id_str}{dv_str}{ch_str} "
                    f"n={j['n_tickers']} ok={j['n_success']} "
                    f"t={j['elapsed']:.1f}s"
                )
    except Exception as e:
        console.print(f"[dim]研究数据库统计不可用: {e}[/dim]")


# ═══════════════════════════════════════════════════════
# clear-cache — 清除缓存
# ═══════════════════════════════════════════════════════


def clear_cache() -> None:
    """清除所有缓存（K线/TA/Kronos），不影响研究数据库。"""
    _load_env()

    from trade_krono_cli.cache import get_cache

    n = get_cache().clear_all()
    console.print(f"[yellow]🧹 已清除 {n} 条缓存[/yellow]")


# ═══════════════════════════════════════════════════════
# warm-cache — 盘前缓存预热
# ═══════════════════════════════════════════════════════


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


# ═══════════════════════════════════════════════════════
# sync-universe — 全量 A 股 K 线缓存同步
# ═══════════════════════════════════════════════════════


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


# ═══════════════════════════════════════════════════════
# sync-whitelist — 仅同步白名单股票
# ═══════════════════════════════════════════════════════


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
        console.print(
            "[red]❌ 未配置 SYNC_WHITELIST，请在 .env 中设置白名单股票代码[/red]"
        )
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
        f"[bold green]🔥 白名单 K 线缓存同步[/bold green] "
        f"股票数={total} 日期={start_date}~{date}"
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


# ═══════════════════════════════════════════════════════
# history — 查看历史分析记录
# ═══════════════════════════════════════════════════════


def history(
    ticker: str | None = typer.Option(
        None, "--ticker", "-t", help="指定股票代码，查看该股票的历史分析记录"
    ),
    limit: int = typer.Option(10, "--limit", "-l", help="最多显示条数"),
) -> None:
    """查看历史分析记录（研究数据库）。"""
    _load_env()

    from trade_krono_cli.research_db import get_research

    research = get_research()

    if ticker:
        ticker = ticker.strip().lower()
        records = research.query_history(ticker, limit=limit)
        if not records:
            console.print(f"[yellow]⚠️  未找到 {ticker} 的历史记录[/yellow]")
            return
        table = Table(title=f"📈 {ticker} 历史分析记录")
        for col in (
            "日期",
            "RunID",
            "数据版本",
            "排名",
            "综合分",
            "TA信号",
            "TA置信",
            "Kronos方向",
            "预期%",
        ):
            table.add_column(
                col,
                justify="right" if col not in ("日期", "RunID", "数据版本") else "left",
            )
        for r in records:
            change = f"{r['kronos_change']:.2f}" if r.get("kronos_change") is not None else "-"
            table.add_row(
                str(r["date"]),
                str(r.get("run_id") or "-"),
                str(r.get("data_version") or "-"),
                str(r["rank"] or "-"),
                (f"{r['composite_score']:.1f}" if r.get("composite_score") else "-"),
                str(r["ta_signal"] or "-"),
                (f"{r['ta_confidence']:.0f}" if r.get("ta_confidence") else "-"),
                str(r["kronos_direction"] or "-"),
                change,
            )
        console.print(table)
    else:
        jobs = research.list_jobs(limit=limit)
        if not jobs:
            console.print("[dim]研究数据库中暂无分析记录[/dim]")
            return
        table = Table(title="📋 最近分析作业")
        for col in (
            "作业ID",
            "RunID",
            "日期",
            "股票数",
            "成功数",
            "数据版本",
            "耗时(s)",
        ):
            table.add_column(
                col,
                justify="right" if col not in ("作业ID", "RunID") else "left",
            )
        for j in jobs:
            run_id = j.get("run_id", "-") or "-"
            dv = j.get("data_version", "-") or "-"
            table.add_row(
                j["job_id"],
                run_id,
                j["date"],
                str(j["n_tickers"]),
                str(j["n_success"]),
                dv,
                f"{j['elapsed']:.1f}",
            )
        console.print(table)


# ═══════════════════════════════════════════════════════
# eval — 预测评估
# ═══════════════════════════════════════════════════════


def eval_prediction(
    from_date: str | None = typer.Option(None, "--from", "-f", help="起始分析日期 YYYY-MM-DD"),
    to_date: str | None = typer.Option(None, "--to", "-t", help="截止分析日期 YYYY-MM-DD"),
    tickers: str | None = typer.Option(None, "--tickers", "-i", help="只评估指定股票（逗号分隔）"),
    latest: bool = typer.Option(False, "--latest", "-l", help="查看最新评估结果（不重新计算）"),
    backtest: bool = typer.Option(
        False,
        "--backtest",
        "-b",
        help="运行回测引擎，输出年化收益/夏普/最大回撤等绩效指标",
    ),
    rebal_mode: str = typer.Option(
        "fixed_horizon",
        "--rebal-mode",
        help="调仓模式: fixed_horizon / rebal_weekly / rebal_monthly",
    ),
) -> None:
    """预测评估：验证历史预测的准确性。"""
    _load_env()

    from trade_krono_cli.prediction_eval import run_evaluation

    ticker_list = None
    if tickers:
        ticker_list = [x.strip() for x in tickers.split(",") if x.strip()]

    run_evaluation(
        from_date=from_date,
        to_date=to_date,
        tickers=ticker_list,
        latest=latest,
        backtest=backtest,
        rebal_mode=rebal_mode,
    )


# ═══════════════════════════════════════════════════════
# retry-failed — 重跑失败股票
# ═══════════════════════════════════════════════════════


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
