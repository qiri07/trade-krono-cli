"""
trade-krono-cli CLI 入口 — Typer 实现。

支持命令：
  run        一键运行（TA + Kronos 并行）
  ta         仅 TradingAgents 分析
  kronos     仅 Kronos 预测
  status     查看系统状态
  history    查看历史分析记录
  repo       外部项目管理（status / doctor / update / pin）
  clear-cache  清除缓存
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from trade_krono_cli.report import print_table, print_summary

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


@repo_app.command()
def repo_status():
    """查看所有外部 repo 的状态（分支、commit、dirty、pinned、lock 漂移）。"""
    from trade_krono_cli.external import status
    entries = status()
    if not entries:
        console.print("[yellow]⚠️  未检测到外部 repo 配置[/yellow]")
        return

    table = Table(title="📦 外部 Repo 状态", header_style="bold cyan")
    for col in ("Repo", "路径", "分支", "Commit", "Pinned", "Locked", "Dirty", "状态"):
        table.add_column(col, justify="left" if col in ("Repo", "路径", "状态") else "center")
    for e in entries:
        path_str = str(e.path_exists)
        branch = e.branch or "?"
        commit = e.commit_short or (e.commit[:12] if e.commit else "?")
        pinned = "✅" if e.is_pinned else "—"
        locked = "📌" if e.is_locked else "—"
        dirty = "⚠️" if e.is_dirty else "—"
        if not e.path_exists:
            state = "[red]不存在[/red]"
        elif not e.is_git_repo:
            state = "[yellow]非 git[/yellow]"
        elif e.lock_mismatch:
            state = f"[red]lock漂移[/red]"
        elif e.error:
            state = f"[red]{e.error}[/red]"
        elif e.is_up_to_date is True:
            state = "[green]最新[/green]"
        elif e.is_up_to_date is False:
            state = "[yellow]落后[/yellow]"
        else:
            state = "—"
        table.add_row(e.name, path_str, branch, commit, pinned, locked, dirty, state)
    console.print(table)


@repo_app.command()
def repo_doctor():
    """诊断外部 repo 问题，列出所有需要关注的项。"""
    from trade_krono_cli.external import doctor, status, load_lock
    issues = doctor()
    entries = status()
    lock = load_lock()

    if not issues and entries:
        console.print("[green]✅ 所有外部 repo 状态正常[/green]")
        # 显示当前 pin/lock 状态
        for e in entries:
            if e.is_pinned and e.commit:
                console.print(f"  📌 [{e.name}] pinned → {e.commit[:12]}")
            elif e.is_locked and e.lock_commit:
                console.print(f"  🔒 [{e.name}] locked  → {e.lock_commit}（未 pinned，跟踪 branch）")
            elif e.branch:
                console.print(f"  🌿 [{e.name}] tracking → {e.branch}")
        # 显示 lock 文件时间戳
        if lock.get("generated_at"):
            console.print(f"\n  [dim]repo.lock 最后更新: {lock['generated_at']}[/dim]")
        return

    if not entries:
        console.print("[yellow]⚠️  未检测到外部 repo 配置[/yellow]")
        console.print("  建议：创建 external/repos.yaml 或使用默认路径")
        raise typer.Exit(1)

    console.print("[bold red]❌ 检测到以下问题：[/bold red]")
    for issue in issues:
        console.print(f"  {issue}")

    console.print("\n[dim]💡 修复建议：[/dim]")
    console.print("  • 路径不存在  → 将项目 clone 到指定路径，或编辑 external/repos.yaml")
    console.print("  • 非 git repo → 初始化 git：git init")
    console.print("  • dirty       → git stash 或 git checkout -- .")
    console.print("  • lock 漂移   → 运行 repo pin <name> <commit> 重新锁定")
    console.print("  • 落后于远程  → 运行 repo update")
    raise typer.Exit(1)


@repo_app.command()
def repo_update():
    """拉取所有外部 repo 的最新代码（仅 unpinned repos），并刷新 repo.lock。"""
    from trade_krono_cli.external import update, get_repos
    repos = get_repos()
    pinned = [r.name for r in repos if r.commit]
    if pinned:
        console.print(f"[yellow]⚠️  以下 repo 已 pinned，跳过 update：{', '.join(pinned)}[/yellow]")
        console.print("  （pinned repo 需手动 git checkout 后再 update）")

    results = update()
    for name, msg in results.items():
        console.print(f"  {msg}")


@repo_app.command()
def repo_pin(
    name: str = typer.Argument(..., help="repo 名称：tradingagents / kronos"),
    commit: str = typer.Argument(..., help="commit SHA（长或短均可）"),
):
    """将外部 repo pin 到指定 commit，同时更新 repos.yaml 和 repo.lock。

    示例：
      trade-krono-cli repo pin tradingagents abc1234
      trade-krono-cli repo pin kronos def5678
    """
    from trade_krono_cli.external import pin
    try:
        pin(name, commit)
        console.print(f"[green]✅ [{name}] 已 pin 到 {commit[:12]}[/green]")
        console.print(f"   配置文件已更新：external/repos.yaml + external/repo.lock")
    except ValueError as e:
        console.print(f"[red]❌ {e}[/red]")
        raise typer.Exit(1)


def _sanitize_path(path: str, label: str, project_root: Path) -> Path:
    """验证输出路径在项目根目录内，防止路径遍历。"""
    p = Path(path).resolve()
    try:
        p.relative_to(project_root)
    except ValueError:
        console.print(
            f"[red]❌ {label} 路径必须在项目根目录下: {path}[/red]"
        )
        raise typer.Exit(1)
    return p


def _load_env() -> None:
    """启动时初始化配置和日志。"""
    import os
    from dotenv import load_dotenv
    from trade_krono_cli.config import get_settings
    from trade_krono_cli.logger import setup_logger

    # 加载 .env
    project_root = Path(__file__).resolve().parent.parent
    load_dotenv(project_root / ".env", override=False)

    # 初始化日志
    s = get_settings()
    log_file = s.cache_dir.parent / "pipeline.log"
    try:
        setup_logger(level="INFO", log_file=log_file)
    except Exception as e:
        import loguru
        loguru.logger.remove()
        loguru.logger.add(sys.stderr, level="INFO")
        loguru.logger.warning(f"日志文件初始化失败，降级到控制台: {e}")


def _load_tickers(
    tickers_str: Optional[str], config_file: Optional[str]
) -> list[str]:
    """从命令行或配置文件加载股票列表。"""
    if tickers_str:
        return [x.strip() for x in tickers_str.split(",") if x.strip()]
    if config_file:
        path = Path(config_file)
        if not path.exists():
            console.print(f"[red]❌ 配置文件不存在: {path}[/red]")
            raise typer.Exit(1)
        tickers = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                tickers.append(line)
        return tickers
    return []


# ═══════════════════════════════════════════════════════
# run — 一键并行运行
# ═══════════════════════════════════════════════════════

@app.command()
def run(
    tickers: Optional[str] = typer.Option(
        None, "--tickers", "-t",
        help="逗号分隔的股票代码，如 600519,000858,600036"
    ),
    config: Optional[str] = typer.Option(
        None, "--config", "-c",
        help="股票列表文件路径（每行一只，支持 # 注释）"
    ),
    date: str = typer.Option(
        ..., "--date", "-d",
        help="分析日期 YYYY-MM-DD"
    ),
    min_confidence: float = typer.Option(
        55.0, "--min-confidence",
        help="最低 TA 置信度"
    ),
    signals: str = typer.Option(
        "BUY,HOLD", "--signals",
        help="允许的 TA 信号，逗号分隔"
    ),
    skip_kronos: bool = typer.Option(
        False, "--skip-kronos",
        help="跳过 Kronos 预测，仅运行 TA"
    ),
    pred_len: int = typer.Option(
        30, "--pred-len",
        help="Kronos 预测步长"
    ),
    lookback: int = typer.Option(
        400, "--lookback",
        help="Kronos 历史回看长度"
    ),
    json_out: str = typer.Option(
        "outputs/results.json", "--json",
        help="JSON 输出路径"
    ),
    html_out: str = typer.Option(
        "outputs/report.html", "--html",
        help="HTML 报告路径"
    ),
    no_cache: bool = typer.Option(
        False, "--no-cache",
        help="禁用缓存"
    ),
    sample_count: int = typer.Option(
        None, "--sample-count",
        help="Kronos 采样次数（默认 5，设 1 为快速模式）"
    ),
    config_file: Optional[str] = typer.Option(
        None, "--config", "-c",
        help="Pipeline 配置文件路径（YAML/JSON，覆盖默认配置）"
    ),
):
    """🔥 一键运行完整流水线（TA 与 Kronos 并行）。"""
    _load_env()

    from trade_krono_cli.pipeline import QuantPipeline
    from trade_krono_cli.pipeline_config import PipelineConfig

    tk_list = _load_tickers(tickers, config)
    if not tk_list:
        console.print("[red]❌ 股票列表为空（请通过 --tickers 或 --config 提供）[/red]")
        raise typer.Exit(1)

    signals_tuple = tuple(x.strip().upper() for x in signals.split(","))

    console.print(f"[bold green]🚀 启动流水线[/bold green] {len(tk_list)} 只 → {date}")

    # 进度回调
    def _progress(stage: str, cur: int, total: int) -> None:
        console.print(f"  [cyan]{stage}[/cyan] [{cur}/{total}]")

    # 输出路径校验
    project_root = Path(__file__).resolve().parent.parent
    json_out_p = _sanitize_path(json_out, "JSON", project_root)
    html_out_p = _sanitize_path(html_out, "HTML", project_root)

    pipeline = QuantPipeline(
        skip_kronos=skip_kronos,
        min_confidence=min_confidence,
        allowed_signals=signals_tuple,
        no_cache=no_cache,
        sample_count=sample_count,
        config=PipelineConfig.load(config_file) if config_file else None,
    )

    merged = pipeline.run_parallel(
        tickers=tk_list,
        date=date,
        output_json=str(json_out_p),
        output_html=str(html_out_p),
        progress_cb=_progress,
    )

    if merged:
        print_table(merged)
        print_summary(merged, date)

    console.print(f"[bold green]✅ 完成[/bold green] → {json_out}")


# ═══════════════════════════════════════════════════════
# ta — 仅 TradingAgents 分析
# ═══════════════════════════════════════════════════════

@app.command()
def ta(
    tickers: Optional[str] = typer.Option(None, "--tickers", "-t"),
    config: Optional[str] = typer.Option(None, "--config", "-c"),
    date: str = typer.Option(..., "--date", "-d"),
    output: str = typer.Option("outputs/ta_result.json", "--output", "-o"),
):
    """仅运行 TradingAgents 选股分析。"""
    _load_env()

    from trade_krono_cli.pipeline import QuantPipeline

    tk_list = _load_tickers(tickers, config)
    if not tk_list:
        console.print("[red]❌ 股票列表为空[/red]")
        raise typer.Exit(1)

    project_root = Path(__file__).resolve().parent.parent
    output_p = _sanitize_path(output, "TA输出", project_root)

    pipeline = QuantPipeline()
    results = pipeline.run_ta_only(tk_list, date, output=str(output_p))

    console.print(f"[green]✅ TA 分析完成 → {output}[/green]")
    console.print(f"   成功: {sum(1 for r in results if r.error is None)}/{len(results)}")


# ═══════════════════════════════════════════════════════
# kronos — 仅 Kronos 预测
# ═══════════════════════════════════════════════════════

@app.command()
def kronos(
    tickers: Optional[str] = typer.Option(None, "--tickers", "-t"),
    config: Optional[str] = typer.Option(None, "--config", "-c"),
    date: str = typer.Option(..., "--date", "-d"),
    pred_len: int = typer.Option(30, "--pred-len"),
    lookback: int = typer.Option(400, "--lookback"),
    sample_count: int = typer.Option(
        None, "--sample-count",
        help="Kronos 采样次数（默认 5，设 1 为快速模式）"
    ),
    config_file: Optional[str] = typer.Option(
        None, "--config", "-c",
        help="Pipeline 配置文件路径（YAML/JSON，覆盖默认配置）"
    ),
    output: str = typer.Option("outputs/kronos_result.json", "--output", "-o"),
):
    """仅运行 Kronos 批量预测。"""
    _load_env()

    from trade_krono_cli.pipeline import QuantPipeline
    from trade_krono_cli.pipeline_config import PipelineConfig

    tk_list = _load_tickers(tickers, config)
    if not tk_list:
        console.print("[red]❌ 股票列表为空[/red]")
        raise typer.Exit(1)

    project_root = Path(__file__).resolve().parent.parent
    output_p = _sanitize_path(output, "Kronos输出", project_root)

    pipeline = QuantPipeline(
        sample_count=sample_count,
        config=PipelineConfig.load(config_file) if config_file else None,
    )
    results = pipeline.run_kronos_only(tk_list, date, output=str(output_p))

    console.print(f"[green]✅ Kronos 预测完成 → {output}[/green]")
    console.print(f"   成功: {sum(1 for r in results if r.error is None)}/{len(results)}")


# ═══════════════════════════════════════════════════════
# status — 查看系统状态
# ═══════════════════════════════════════════════════════

@app.command()
def status():
    """查看系统状态：密钥、缓存、模型配置。"""
    _load_env()

    from trade_krono_cli.config import get_settings
    from trade_krono_cli.security import KeyVault
    from trade_krono_cli.cache import get_cache

    s = get_settings()
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

    # 缓存统计
    try:
        cache_stats = get_cache().stats()
        console.print(f"[dim]缓存: {cache_stats}[/dim]")
    except Exception as e:
        console.print(f"[dim]缓存统计不可用: {e}[/dim]")

    # 研究数据库统计
    try:
        from trade_krono_cli.cache import get_research
        res_stats = get_research().stats()
        console.print(f"[dim]研究数据库: {res_stats}[/dim]")
        # 最近作业
        jobs = get_research().list_jobs(limit=5)
        if jobs:
            console.print("[bold]最近分析作业:[/bold]")
            for j in jobs:
                run_id_str = f" run={j.get('run_id', '-')}" if j.get("run_id") else ""
                dv_str = f" data={j.get('data_version', '-')}" if j.get("data_version") else ""
                ch_str = f" hash={j.get('config_hash', '-')[:8]}…" if j.get("config_hash") else ""
                console.print(
                    f"  • [{j['date']}] job={j['job_id']}{run_id_str}{dv_str}{ch_str} "
                    f"n={j['n_tickers']} ok={j['n_success']} "
                    f"t={j['elapsed']:.1f}s"
                )
    except Exception as e:
        console.print(f"[dim]研究数据库统计不可用: {e}[/dim]")


# ═══════════════════════════════════════════════════════
# clear-cache — 清除缓存
# ═══════════════════════════════════════════════════════

@app.command()
def clear_cache():
    """清除所有缓存（K线/TA/Kronos），不影响研究数据库。"""
    _load_env()

    from trade_krono_cli.cache import get_cache
    n = get_cache().clear_all()
    console.print(f"[yellow]🧹 已清除 {n} 条缓存[/yellow]")


# ═══════════════════════════════════════════════════════
# history — 查看历史分析记录
# ═══════════════════════════════════════════════════════

@app.command()
def history(
    ticker: Optional[str] = typer.Option(
        None, "--ticker", "-t",
        help="指定股票代码，查看该股票的历史分析记录"
    ),
    limit: int = typer.Option(10, "--limit", "-l", help="最多显示条数"),
):
    """查看历史分析记录（研究数据库）。"""
    _load_env()

    from trade_krono_cli.cache import get_research
    research = get_research()

    if ticker:
        ticker = ticker.strip().lower()
        records = research.query_history(ticker, limit=limit)
        if not records:
            console.print(f"[yellow]⚠️  未找到 {ticker} 的历史记录[/yellow]")
            return
        table = Table(title=f"📈 {ticker} 历史分析记录")
        for col in ("日期", "RunID", "数据版本", "排名", "综合分", "TA信号", "TA置信", "Kronos方向", "预期%"):
            table.add_column(col, justify="right" if col not in ("日期", "RunID", "数据版本") else "left")
        for r in records:
            change = (
                f"{r['kronos_change']:.2f}"
                if r.get("kronos_change") is not None
                else "-"
            )
            table.add_row(
                str(r["date"]),
                str(r.get("run_id") or "-"),
                str(r.get("data_version") or "-"),
                str(r["rank"] or "-"),
                f"{r['composite_score']:.1f}" if r.get("composite_score") else "-",
                str(r["ta_signal"] or "-"),
                f"{r['ta_confidence']:.0f}" if r.get("ta_confidence") else "-",
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
        for col in ("作业ID", "RunID", "日期", "股票数", "成功数", "数据版本", "耗时(s)"):
            table.add_column(col, justify="right" if col not in ("作业ID", "RunID") else "left")
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

@app.command()
def eval_prediction(
    from_date: Optional[str] = typer.Option(
        None, "--from", "-f",
        help="起始分析日期 YYYY-MM-DD"
    ),
    to_date: Optional[str] = typer.Option(
        None, "--to", "-t",
        help="截止分析日期 YYYY-MM-DD"
    ),
    tickers: Optional[str] = typer.Option(
        None, "--tickers", "-i",
        help="只评估指定股票（逗号分隔）"
    ),
    latest: bool = typer.Option(
        False, "--latest", "-l",
        help="查看最新评估结果（不重新计算）"
    ),
):
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
    )


# ═══════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════

def main():
    app()


if __name__ == "__main__":
    main()
