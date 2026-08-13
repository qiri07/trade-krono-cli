"""
CLI 命令实现 — 各命令的业务逻辑。

cli.py 负责 app 注册和入口，本模块负责命令实现。
测试中可直接导入辅助函数：from trade_krono_cli.cli import _load_tickers, _sanitize_path
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from trade_krono_cli.config import Settings, get_settings
from trade_krono_cli.logger import setup_logger
from trade_krono_cli.pipeline.reporter import print_table, print_summary

console: Console = Console()


# ═══════════════════════════════════════════════════════
# 降级策略辅助函数
# ═══════════════════════════════════════════════════════

def _build_degrade_overrides(
    degrade_mode: str = "strict",
    ta_cache_fallback: bool = False,
) -> dict:
    """根据 CLI 参数构建降级策略覆盖字典。"""
    overrides: dict = {}
    if degrade_mode != "strict":
        overrides["degrade_mode"] = degrade_mode
    if ta_cache_fallback:
        overrides["ta_cache_fallback_enabled"] = True
    return overrides


# ═══════════════════════════════════════════════════════
# 工具函数（同步到 cli.py 的导出中）
# ═══════════════════════════════════════════════════════

def _sanitize_path(path: str, label: str, project_root: Path) -> Path:
    """验证输出路径在项目根目录内，防止路径遍历与符号链接绕过。"""
    real_project = os.path.realpath(str(project_root))
    real_path = os.path.realpath(path)

    # 拒绝：目标路径不在 project_root 的 realpath 之下
    try:
        Path(real_path).relative_to(real_project)
    except ValueError:
        console.print(
            f"[red]❌ {label} 路径必须在项目根目录下: {path}[/red]"
        )
        raise typer.Exit(1)

    # 拒绝：路径中存在指向 project_root 之外的符号链接
    # 逐段向上检查，发现越界链接即拒绝
    p = Path(real_path)
    while str(p) != real_project and str(p).startswith(real_project + os.sep):
        if p.is_symlink():
            target = os.path.realpath(str(p))
            try:
                Path(target).relative_to(real_project)
            except ValueError:
                console.print(
                    f"[red]❌ {label} 路径包含指向项目外的符号链接: {path}[/red]"
                )
                raise typer.Exit(1)
        p = p.parent

    return Path(real_path)


def _load_env() -> tuple[Settings, Path]:
    """启动时初始化配置和日志。返回 (Settings, log_file_path)。"""
    from dotenv import load_dotenv

    project_root = Path(__file__).resolve().parent.parent
    load_dotenv(project_root / ".env", override=False)

    s = get_settings()

    # ── 配置校验（启动前一次性执行）───────────────────────────────
    from trade_krono_cli.config import run_validation
    errors, warnings = run_validation()
    for w in warnings:
        console.print(f"  [yellow]{w}[/yellow]")
    if errors:
        console.print("[bold red]❌ 配置校验失败，请修复后再运行：[/bold red]")
        for e in errors:
            console.print(f"  [red]{e}[/red]")
        raise typer.Exit(1)

    log_file = s.cache_dir.parent / "pipeline.log"
    try:
        setup_logger(level="INFO", log_file=log_file, settings=s)
    except Exception as e:
        import loguru
        loguru.logger.remove()
        loguru.logger.add(sys.stderr, level="INFO")
        loguru.logger.warning(f"日志文件初始化失败，降级到控制台: {e}")

    return s, log_file


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
# repo 子命令组（不带装饰器；由 cli.py 显式注册）
# ═══════════════════════════════════════════════════════

def repo_status() -> None:
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


def repo_doctor() -> None:
    """诊断外部 repo 问题，列出所有需要关注的项。"""
    from trade_krono_cli.external import doctor, status, load_lock
    issues = doctor()
    entries = status()
    lock = load_lock()

    if not issues and entries:
        console.print("[green]✅ 所有外部 repo 状态正常[/green]")
        for e in entries:
            if e.is_pinned and e.commit:
                console.print(f"  📌 [{e.name}] pinned → {e.commit[:12]}")
            elif e.is_locked and e.lock_commit:
                console.print(
                    f"  🔒 [{e.name}] locked  → {e.lock_commit}"
                    "（未 pinned，跟踪 branch）"
                )
            elif e.branch:
                console.print(f"  🌿 [{e.name}] tracking → {e.branch}")
        if lock.get("generated_at"):
            console.print(
                f"\n  [dim]repo.lock 最后更新: {lock['generated_at']}[/dim]"
            )
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


def repo_update() -> None:
    """拉取所有外部 repo 的最新代码（仅 unpinned repos），并刷新 repo.lock。"""
    from trade_krono_cli.external import update, get_repos
    repos = get_repos()
    pinned = [r.name for r in repos if r.commit]
    if pinned:
        console.print(
            f"[yellow]⚠️  以下 repo 已 pinned，跳过 update："
            f"{', '.join(pinned)}[/yellow]"
        )
        console.print("  （pinned repo 需手动 git checkout 后再 update）")

    results = update()
    for name, msg in results.items():
        console.print(f"  {msg}")


def repo_pin(
    name: str = typer.Argument(..., help="repo 名称：tradingagents / kronos"),
    commit: str = typer.Argument(..., help="commit SHA（长或短均可）"),
) -> None:
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


# ═══════════════════════════════════════════════════════
# run — 一键并行运行
# ═══════════════════════════════════════════════════════

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
    market_cap_range: Optional[str] = typer.Option(
        None, "--market-cap",
        help="市值范围（亿元），如 \"50,5000\""
    ),
    industry_whitelist: Optional[str] = typer.Option(
        None, "--industry-whitelist",
        help="行业白名单，逗号分隔，如 \"银行,食品饮料\""
    ),
    industry_blacklist: Optional[str] = typer.Option(
        None, "--industry-blacklist",
        help="行业黑名单，逗号分隔，如 \"房地产,煤炭\""
    ),
    pe_range: Optional[str] = typer.Option(
        None, "--pe-range",
        help="PE 区间，如 \"5,30\""
    ),
    pb_range: Optional[str] = typer.Option(
        None, "--pb-range",
        help="PB 区间，如 \"0,3\""
    ),
    max_risk_score: Optional[float] = typer.Option(
        None, "--max-risk-score",
        help="风险分上限（0-1）"
    ),
    min_volume_ratio: Optional[float] = typer.Option(
        None, "--min-volume-ratio",
        help="最小量比"
    ),
    exclude_st: bool = typer.Option(
        True, "--exclude-st/--include-st",
        help="是否排除 ST 股票（默认排除）"
    ),
    sample_count: int = typer.Option(
        None, "--sample-count",
        help="Kronos 采样次数（默认 5，设 1 为快速模式）"
    ),
    scoring_strategy: str = typer.Option(
        "linear", "--scoring-strategy",
        help="综合打分策略: linear / multiplicative / rank_based",
        rich_help_panel="评分策略",
    ),
    risk_boost_strategy: str = typer.Option(
        "fixed_boost", "--risk-boost-strategy",
        help="风险加分策略: fixed_boost / scaled_boost / diminishing_boost",
        rich_help_panel="评分策略",
    ),
    risk_boost_multiplier: float = typer.Option(
        1.0, "--risk-boost-multiplier",
        help="scaled_boost 倍率 (0, 5.0]",
        rich_help_panel="评分策略",
    ),
    risk_boost_diminishing_power: float = typer.Option(
        0.5, "--risk-boost-power",
        help="diminishing_boost 幂次 (0, 1.0]，默认 0.5 (= √n)",
        rich_help_panel="评分策略",
    ),
    config_file: Optional[str] = typer.Option(
        None, "--config", "-c",
        help="Pipeline 配置文件路径（YAML/JSON，覆盖默认配置）"
    ),
    streaming: bool = typer.Option(
        False, "--streaming", "-s",
        help="启用流式模式：数据拉取与计算重叠执行，总耗时≈max(fetch,compute)"
    ),
    max_retries: int = typer.Option(
        None, "--max-retries",
        help="最大重试次数（含首次，默认 3）",
        rich_help_panel="重试策略",
    ),
    base_delay: float = typer.Option(
        None, "--base-delay",
        help="基础退避秒数（默认 2.0）",
        rich_help_panel="重试策略",
    ),
    no_jitter: bool = typer.Option(
        False, "--no-jitter",
        help="禁用随机抖动",
        rich_help_panel="重试策略",
    ),
    no_rate_limit_backoff: bool = typer.Option(
        False, "--no-rate-limit-backoff",
        help="禁用限流自适应退避（不解析 Retry-After 头）",
        rich_help_panel="重试策略",
    ),
    degrade_mode: str = typer.Option(
        "strict", "--degrade-mode",
        help="降级策略：strict / ta_only_on_kronos_fail / ta_cache_fallback",
        rich_help_panel="降级策略",
    ),
    ta_cache_fallback: bool = typer.Option(
        False, "--ta-cache-fallback",
        help="启用 TA 缓存回退（需配合 --degrade-mode ta_cache_fallback）",
        rich_help_panel="降级策略",
    ),
) -> None:
    """🔥 一键运行完整流水线（TA 与 Kronos 并行）。

    --streaming 时启用流式模式：数据拉取与模型推理重叠执行，
    总耗时从 T_fetch + T_compute 缩短为 max(T_fetch, T_compute)。
    """
    _load_env()

    from trade_krono_cli.pipeline import QuantPipeline
    from trade_krono_cli.pipeline_config import PipelineConfig

    tk_list = _load_tickers(tickers, config)
    if not tk_list:
        console.print(
            "[red]❌ 股票列表为空"
            "（请通过 --tickers 或 --config 提供）[/red]"
        )
        raise typer.Exit(1)

    signals_tuple = tuple(x.strip().upper() for x in signals.split(","))

    # 解析过滤配置（CLI 参数优先，其次 fallback 到 Settings 默认）
    from trade_krono_cli.pipeline_config import _parse_range, _parse_comma_list
    mc_range = _parse_range(market_cap_range) if market_cap_range else None
    ind_whitelist = _parse_comma_list(industry_whitelist) if industry_whitelist else None
    ind_blacklist = _parse_comma_list(industry_blacklist) if industry_blacklist else None
    pe_r = _parse_range(pe_range) if pe_range else None
    pb_r = _parse_range(pb_range) if pb_range else None

    # 构建覆盖配置的 override dict
    filter_overrides: dict = {}
    if mc_range: filter_overrides["market_cap_range"] = mc_range
    if ind_whitelist: filter_overrides["industry_whitelist"] = ind_whitelist
    if ind_blacklist: filter_overrides["industry_blacklist"] = ind_blacklist
    if pe_r: filter_overrides["pe_range"] = pe_r
    if pb_r: filter_overrides["pb_range"] = pb_r
    if max_risk_score is not None: filter_overrides["max_risk_score"] = max_risk_score
    if min_volume_ratio is not None: filter_overrides["min_volume_ratio"] = min_volume_ratio
    if not exclude_st: filter_overrides["exclude_st"] = False

    # 评分策略覆盖（CLI 优先于环境变量）
    strategy_overrides: dict = {}
    if scoring_strategy != "linear":
        strategy_overrides["scoring_strategy"] = {"strategy": scoring_strategy}
    risk_boost_overrides: dict = {"strategy": risk_boost_strategy}
    if risk_boost_multiplier != 1.0:
        risk_boost_overrides["multiplier"] = risk_boost_multiplier
    if risk_boost_diminishing_power != 0.5:
        risk_boost_overrides["diminishing_power"] = risk_boost_diminishing_power
    strategy_overrides["risk_boost_strategy"] = risk_boost_overrides

    console.print(
        f"[bold green]🚀 启动流水线[/bold green] "
        f"{len(tk_list)} 只 → {date}"
    )

    def _progress(stage: str, cur: int, total: int) -> None:
        console.print(f"  [cyan]{stage}[/cyan] [{cur}/{total}]")

    project_root = Path(__file__).resolve().parent.parent
    json_out_p = _sanitize_path(json_out, "JSON", project_root)
    html_out_p = _sanitize_path(html_out, "HTML", project_root)

    all_overrides = {**filter_overrides, **strategy_overrides}

    # 重试策略覆盖
    retry_overrides: dict = {}
    if max_retries is not None:
        retry_overrides["retry_max_attempts"] = max_retries
    if base_delay is not None:
        retry_overrides["retry_base_delay"] = base_delay
    if no_jitter:
        retry_overrides["retry_jitter"] = False
    if no_rate_limit_backoff:
        retry_overrides["retry_rate_limit_backoff"] = False
    if retry_overrides:
        all_overrides.update(retry_overrides)

    # 降级策略覆盖
    degrade_overrides = _build_degrade_overrides(degrade_mode, ta_cache_fallback)
    if degrade_overrides:
        all_overrides.update(degrade_overrides)

    pipeline = QuantPipeline(
        skip_kronos=skip_kronos,
        min_confidence=min_confidence,
        allowed_signals=signals_tuple,
        no_cache=no_cache,
        sample_count=sample_count,
        config=(
            PipelineConfig.load(config_file).override(**all_overrides)
            if config_file else PipelineConfig.default().override(**all_overrides)
        ) if all_overrides else (
            PipelineConfig.load(config_file) if config_file else None
        ),
    )

    merged = pipeline.run_parallel(
        tickers=tk_list,
        date=date,
        output_json=str(json_out_p),
        output_html=str(html_out_p),
        progress_cb=_progress,
        streaming=streaming,
    )

    if merged:
        print_table(merged)
        print_summary(merged, date)

    console.print(f"[bold green]✅ 完成[/bold green] → {json_out}")


# ═══════════════════════════════════════════════════════
# ta — 仅 TradingAgents 分析
# ═══════════════════════════════════════════════════════

def ta(
    tickers: Optional[str] = typer.Option(None, "--tickers", "-t"),
    config: Optional[str] = typer.Option(None, "--config", "-c"),
    date: str = typer.Option(..., "--date", "-d"),
    output: str = typer.Option("outputs/ta_result.json", "--output", "-o"),
    max_retries: int = typer.Option(
        None, "--max-retries",
        help="最大重试次数（含首次，默认 3）",
        rich_help_panel="重试策略",
    ),
    base_delay: float = typer.Option(
        None, "--base-delay",
        help="基础退避秒数（默认 2.0）",
        rich_help_panel="重试策略",
    ),
    no_jitter: bool = typer.Option(
        False, "--no-jitter",
        help="禁用随机抖动",
        rich_help_panel="重试策略",
    ),
    degrade_mode: str = typer.Option(
        "strict", "--degrade-mode",
        help="降级策略：strict / ta_only_on_kronos_fail / ta_cache_fallback",
        rich_help_panel="降级策略",
    ),
    ta_cache_fallback: bool = typer.Option(
        False, "--ta-cache-fallback",
        help="启用 TA 缓存回退（需配合 --degrade-mode ta_cache_fallback）",
        rich_help_panel="降级策略",
    ),
) -> None:
    """仅运行 TradingAgents 选股分析。"""
    _load_env()

    from trade_krono_cli.pipeline import QuantPipeline

    tk_list = _load_tickers(tickers, config)
    if not tk_list:
        console.print("[red]❌ 股票列表为空[/red]")
        raise typer.Exit(1)

    project_root = Path(__file__).resolve().parent.parent
    output_p = _sanitize_path(output, "TA输出", project_root)

    # 构建重试策略
    retry_overrides: dict = {}
    if max_retries is not None:
        retry_overrides["retry_max_attempts"] = max_retries
    if base_delay is not None:
        retry_overrides["retry_base_delay"] = base_delay
    if no_jitter:
        retry_overrides["retry_jitter"] = False
    cfg = PipelineConfig.default().override(**retry_overrides) if retry_overrides else None
    degrade_overrides = _build_degrade_overrides(degrade_mode, ta_cache_fallback)
    if degrade_overrides:
        cfg = (cfg or PipelineConfig.default()).override(**degrade_overrides)
    pipeline = QuantPipeline(config=cfg)
    results = pipeline.run_ta_only(tk_list, date, output=str(output_p))

    console.print(f"[green]✅ TA 分析完成 → {output}[/green]")
    console.print(
        f"   成功: "
        f"{sum(1 for r in results if r.error is None)}/{len(results)}"
    )


# ═══════════════════════════════════════════════════════
# kronos — 仅 Kronos 预测
# ═══════════════════════════════════════════════════════

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
    max_retries: int = typer.Option(
        None, "--max-retries",
        help="最大重试次数（含首次，默认 3）",
        rich_help_panel="重试策略",
    ),
    base_delay: float = typer.Option(
        None, "--base-delay",
        help="基础退避秒数（默认 2.0）",
        rich_help_panel="重试策略",
    ),
    no_jitter: bool = typer.Option(
        False, "--no-jitter",
        help="禁用随机抖动",
        rich_help_panel="重试策略",
    ),
    degrade_mode: str = typer.Option(
        "strict", "--degrade-mode",
        help="降级策略：strict / ta_only_on_kronos_fail / ta_cache_fallback",
        rich_help_panel="降级策略",
    ),
) -> None:
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

    # 构建重试策略
    retry_overrides: dict = {}
    if max_retries is not None:
        retry_overrides["retry_max_attempts"] = max_retries
    if base_delay is not None:
        retry_overrides["retry_base_delay"] = base_delay
    if no_jitter:
        retry_overrides["retry_jitter"] = False
    cfg = PipelineConfig.default().override(**retry_overrides) if retry_overrides else None
    degrade_overrides = _build_degrade_overrides(degrade_mode, ta_cache_fallback=False)
    if degrade_overrides:
        cfg = (cfg or PipelineConfig.default()).override(**degrade_overrides)
    pipeline = QuantPipeline(
        sample_count=sample_count,
        config=cfg or (PipelineConfig.load(config_file) if config_file else None),
    )
    results = pipeline.run_kronos_only(tk_list, date, output=str(output_p))

    console.print(f"[green]✅ Kronos 预测完成 → {output}[/green]")
    console.print(
        f"   成功: "
        f"{sum(1 for r in results if r.error is None)}/{len(results)}"
    )


# ═══════════════════════════════════════════════════════
# status — 查看系统状态
# ═══════════════════════════════════════════════════════

def status() -> None:
    """查看系统状态：密钥、缓存、模型配置 + 健康检查。"""
    s, _ = _load_env()

    from trade_krono_cli.security import KeyVault
    from trade_krono_cli.cache import get_cache

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
                run_id_str = (
                    f" run={j.get('run_id', '-')}"
                    if j.get("run_id") else ""
                )
                dv_str = (
                    f" data={j.get('data_version', '-')}"
                    if j.get("data_version") else ""
                )
                ch_str = (
                    f" hash={j.get('config_hash', '-')[:8]}…"
                    if j.get("config_hash") else ""
                )
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
        help="基准日期 YYYY-MM-DD（默认今天）"
    ),
    lookback: int = typer.Option(
        730, "--lookback", "-l",
        help="回溯天数，默认 730（2年）"
    ),
) -> None:
    """盘前缓存预热：批量拉取 K 线数据并写入缓存。

    历史数据（>30天前）永久缓存，当日/近期数据 1h TTL。
    可显著减少盘中运行的首次数据拉取耗时。
    """
    _load_env()

    from trade_krono_cli.cache import get_cache

    tk_list = _load_tickers(tickers, config)
    if not tk_list:
        console.print(
            "[red]❌ 股票列表为空"
            "（请通过 --tickers 或 --config 提供）[/red]"
        )
        raise typer.Exit(1)

    cache = get_cache()
    total_rows, total_segments = 0, 0
    console.print(
        f"[bold green]🔥 缓存预热[/bold green] "
        f"{len(tk_list)} 只 → {date} (回溯 {lookback} 天)"
    )
    for i, tk in enumerate(tk_list, 1):
        console.print(f"  [{i}/{len(tk_list)}] {tk} ...", end="")
        rows, segs = cache.warm_history(tk, date, lookback_days=lookback)
        total_rows += rows
        total_segments += segs
        console.print(f" ✅ {rows}行/{segs}段")

    console.print(
        f"[bold green]✅ 预热完成[/bold green] "
        f"共 {total_rows} 行 / {total_segments} 个缓存段"
    )


# ═══════════════════════════════════════════════════════
# history — 查看历史分析记录
# ═══════════════════════════════════════════════════════

def history(
    ticker: Optional[str] = typer.Option(
        None, "--ticker", "-t",
        help="指定股票代码，查看该股票的历史分析记录"
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
            "日期", "RunID", "数据版本", "排名", "综合分",
            "TA信号", "TA置信", "Kronos方向", "预期%",
        ):
            table.add_column(
                col,
                justify="right"
                if col not in ("日期", "RunID", "数据版本")
                else "left",
            )
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
                (
                    f"{r['composite_score']:.1f}"
                    if r.get("composite_score") else "-"
                ),
                str(r["ta_signal"] or "-"),
                (
                    f"{r['ta_confidence']:.0f}"
                    if r.get("ta_confidence") else "-"
                ),
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
            "作业ID", "RunID", "日期", "股票数", "成功数",
            "数据版本", "耗时(s)",
        ):
            table.add_column(
                col,
                justify="right"
                if col not in ("作业ID", "RunID")
                else "left",
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
    backtest: bool = typer.Option(
        False, "--backtest", "-b",
        help="运行回测引擎，输出年化收益/夏普/最大回撤等绩效指标",
    ),
    rebal_mode: str = typer.Option(
        "fixed_horizon", "--rebal-mode",
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
        ..., "--date", "-d",
        help="要重跑的日期 YYYY-MM-DD（默认最新有失败的日期）"
    ),
    module: str = typer.Option(
        None, "--module", "-m",
        help="指定模块：ta / kronos（None = 全部）",
    ),
    max_retries: int = typer.Option(
        None, "--max-retries",
        help="最大重试次数（含首次，默认 3）",
        rich_help_panel="重试策略",
    ),
    base_delay: float = typer.Option(
        None, "--base-delay",
        help="基础退避秒数（默认 2.0）",
        rich_help_panel="重试策略",
    ),
    no_jitter: bool = typer.Option(
        False, "--no-jitter",
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

    from trade_krono_cli.retry_policy import get_failure_store
    from trade_krono_cli.pipeline import QuantPipeline
    from trade_krono_cli.pipeline_config import PipelineConfig
    from trade_krono_cli.config import reload_settings

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
        console.print(f"[yellow]⚠️  日期 {date}{f' (module={module})' if module else ''} 无失败记录[/yellow]")
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
    cfg = PipelineConfig.default(settings).override(**retry_overrides) if retry_overrides else PipelineConfig.default(settings)

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
            store.record(ticker, date, orig[0].module, RuntimeError(orig[0].error_message),
                         attempt_count=orig[0].attempt_count + 1)

    console.print(f"\n[bold]{'✅' if still_failed else '🎉'} 重跑完成[/bold]")
    console.print(f"   本次成功: {success_count}/{len(failed_tickers)}")
    if still_failed:
        console.print(f"   仍失败: {len(still_failed)} 只 → {', '.join(still_failed)}")
    else:
        store.clear_for_date(date)
        console.print(f"   已清除日期 {date} 的失败记录")

