"""
CLI 维护命令 — status / clear-cache / warm-cache / history / eval-prediction / retry-failed。
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from trade_krono_cli.cli_commands.core import _load_env, _load_tickers

console: Console = Console()


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
    """盘前缓存预热：批量拉取 K 线数据并写入缓存。

    历史数据（>30天前）永久缓存，当日/近期数据 1h TTL。
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
    retry_overrides: dict = {}
    if max_retries is not None:
        retry_overrides["retry_max_attempts"] = max_retries
    if base_delay is not None:
        retry_overrides["retry_base_delay"] = base_delay
    if no_jitter:
        retry_overrides["retry_jitter"] = False
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
                    success_count += 1
                else:
                    console.print(f"[red]❌ {ta_r.error[:60]}[/red]")
                    still_failed.append(ticker)
            if module is None or module == "kronos":
                if pipeline.kronos is not None:
                    kr = pipeline.kronos.predict_one(ticker, date)
                    if kr.error is None:
                        console.print("[green]✅[/green]")
                        success_count += 1
                    else:
                        console.print(f"[red]❌ {kr.error[:60]}[/red]")
                        still_failed.append(ticker)
                else:
                    console.print("[dim]⊘ (Kronos skipped)[/dim]")
        except Exception as e:
            console.print(f"[red]❌ {type(e).__name__}: {str(e)[:60]}[/red]")
            still_failed.append(ticker)

    # 更新失败记录：成功的移除，仍失败的更新 attempt_count
    succeeded_set = set(failed_tickers) - set(still_failed)
    for ticker in succeeded_set:
        store.clear_for_date(date)  # 简化：清掉该日期所有记录再重建
    store.clear_for_date(date)
    for ticker in still_failed:
        # 找到原始失败记录并更新 attempt_count
        orig = [r for r in fails if r.ticker == ticker]
        if orig:
            store.record(
                ticker,
                date,
                orig[0].module,
                RuntimeError(orig[0].error_message),
                attempt_count=orig[0].attempt_count + 1,
            )

    console.print(f"\n[bold]{'✅' if still_failed else '🎉'} 重跑完成[/bold]")
    console.print(f"   本次成功: {success_count}/{len(failed_tickers)}")
    if still_failed:
        console.print(f"   仍失败: {len(still_failed)} 只 → {', '.join(still_failed)}")
    else:
        store.clear_for_date(date)
        console.print(f"   已清除日期 {date} 的失败记录")
