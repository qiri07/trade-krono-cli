"""CLI 核心命令 — 共享工具函数 + run/ta/kronos 主流程命令。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import typer
from rich.console import Console

from trade_krono_cli.config import Settings, get_settings
from trade_krono_cli.logger import setup_logger
from trade_krono_cli.pipeline.reporter import print_summary, print_table
from trade_krono_cli.pipeline_config import PipelineConfig

console: Console = Console()


# ═══════════════════════════════════════════════════════
# 降级策略辅助函数
# ═══════════════════════════════════════════════════════


def _build_degrade_overrides(
    degrade_mode: str = "strict",
    ta_cache_fallback: bool = False,
) -> dict[str, object]:
    """根据 CLI 参数构建降级策略覆盖字典。"""
    overrides: dict[str, object] = {}
    if degrade_mode != "strict":
        overrides["degrade_mode"] = degrade_mode
    if ta_cache_fallback:
        overrides["ta_cache_fallback_enabled"] = True
    return overrides


def _build_retry_overrides(
    max_retries: int | None = None,
    base_delay: float | None = None,
    no_jitter: bool = False,
    no_rate_limit_backoff: bool = False,
) -> dict[str, object]:
    """根据 CLI 参数构建重试策略覆盖字典。

    供 run / ta / kronos / retry_failed 命令共享使用。
    """
    overrides: dict[str, object] = {}
    if max_retries is not None:
        overrides["retry_max_attempts"] = max_retries
    if base_delay is not None:
        overrides["retry_base_delay"] = base_delay
    if no_jitter:
        overrides["retry_jitter"] = False
    if no_rate_limit_backoff:
        overrides["retry_rate_limit_backoff"] = False
    return overrides


_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


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
        console.print(f"[red]❌ {label} 路径必须在项目根目录下: {path}[/red]")
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
                console.print(f"[red]❌ {label} 路径包含指向项目外的符号链接: {path}[/red]")
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


def _load_tickers(tickers_str: str | None, config_file: str | None) -> list[str]:
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


def run(
    tickers: str | None = typer.Option(
        None, "--tickers", "-t", help="逗号分隔的股票代码，如 600519,000858,600036",
    ),
    stock_file: str | None = typer.Option(
        None, "--stock-file", "-f", help="股票列表文件路径（每行一只，支持 # 注释）",
    ),
    date: str = typer.Option(..., "--date", "-d", help="分析日期 YYYY-MM-DD"),
    min_confidence: float = typer.Option(55.0, "--min-confidence", help="最低 TA 置信度"),
    signals: str = typer.Option("BUY,HOLD", "--signals", help="允许的 TA 信号，逗号分隔"),
    skip_kronos: bool = typer.Option(False, "--skip-kronos", help="跳过 Kronos 预测，仅运行 TA"),
    pred_len: int = typer.Option(30, "--pred-len", help="Kronos 预测步长"),
    lookback: int = typer.Option(400, "--lookback", help="Kronos 历史回看长度"),
    json_out: str = typer.Option("outputs/results.json", "--json", help="JSON 输出路径"),
    html_out: str = typer.Option("outputs/report.html", "--html", help="HTML 报告路径"),
    no_cache: bool = typer.Option(False, "--no-cache", help="禁用缓存"),
    market_cap_range: str | None = typer.Option(
        None, "--market-cap", help='市值范围（亿元），如 "50,5000"',
    ),
    industry_whitelist: str | None = typer.Option(
        None, "--industry-whitelist", help='行业白名单，逗号分隔，如 "银行,食品饮料"',
    ),
    industry_blacklist: str | None = typer.Option(
        None, "--industry-blacklist", help='行业黑名单，逗号分隔，如 "房地产,煤炭"',
    ),
    pe_range: str | None = typer.Option(None, "--pe-range", help='PE 区间，如 "5,30"'),
    pb_range: str | None = typer.Option(None, "--pb-range", help='PB 区间，如 "0,3"'),
    max_risk_score: float | None = typer.Option(None, "--max-risk-score", help="风险分上限（0-1）"),
    min_volume_ratio: float | None = typer.Option(None, "--min-volume-ratio", help="最小量比"),
    min_volume: float | None = typer.Option(
        None, "--min-volume", help="最小成交量（股），低于此值排除（如 10000000 表示 1000 万股）",
    ),
    market_cap_min: float | None = typer.Option(
        None, "--market-cap-min", help="市值最小值（亿元），低于此值排除",
    ),
    exclude_st: bool = typer.Option(
        True, "--exclude-st/--include-st", help="是否排除 ST 股票（默认排除）",
    ),
    sample_count: int = typer.Option(
        None, "--sample-count", help="Kronos 采样次数（默认 5，设 1 为快速模式）",
    ),
    scoring_strategy: str = typer.Option(
        "linear",
        "--scoring-strategy",
        help="综合打分策略: linear / multiplicative / rank_based",
        rich_help_panel="评分策略",
    ),
    risk_boost_strategy: str = typer.Option(
        "fixed_boost",
        "--risk-boost-strategy",
        help="风险加分策略: fixed_boost / scaled_boost / diminishing_boost",
        rich_help_panel="评分策略",
    ),
    risk_boost_multiplier: float = typer.Option(
        1.0,
        "--risk-boost-multiplier",
        help="scaled_boost 倍率 (0, 5.0]",
        rich_help_panel="评分策略",
    ),
    risk_boost_diminishing_power: float = typer.Option(
        0.5,
        "--risk-boost-power",
        help="diminishing_boost 幂次 (0, 1.0]，默认 0.5 (= √n)",
        rich_help_panel="评分策略",
    ),
    config_file: str | None = typer.Option(
        None, "--config", "-c", help="Pipeline 配置文件路径（YAML/JSON，覆盖默认配置）",
    ),
    streaming: bool = typer.Option(
        False,
        "--streaming",
        "-s",
        help="启用流式模式：数据拉取与计算重叠执行，总耗时≈max(fetch,compute)",
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
    no_rate_limit_backoff: bool = typer.Option(
        False,
        "--no-rate-limit-backoff",
        help="禁用限流自适应退避（不解析 Retry-After 头）",
        rich_help_panel="重试策略",
    ),
    degrade_mode: str = typer.Option(
        "strict",
        "--degrade-mode",
        help="降级策略：strict / ta_only_on_kronos_fail / ta_cache_fallback",
        rich_help_panel="降级策略",
    ),
    ta_cache_fallback: bool = typer.Option(
        False,
        "--ta-cache-fallback",
        help="启用 TA 缓存回退（需配合 --degrade-mode ta_cache_fallback）",
        rich_help_panel="降级策略",
    ),
    auto_universe: bool = typer.Option(
        False,
        "--auto-universe",
        help="自动发现全市场 A 股并筛选（忽略 --tickers）",
        rich_help_panel="市场范围",
    ),
    universe_source: str = typer.Option(
        "akshare",
        "--universe-source",
        help="全市场数据源: akshare / mootdx / baostock",
        rich_help_panel="市场范围",
    ),
    max_tickers: int = typer.Option(
        None,
        "--max-tickers",
        help="自动筛选后最多处理的股票数量（用于测试/快速验证）",
        rich_help_panel="市场范围",
    ),
) -> None:
    """🔥 一键运行完整流水线（TA 与 Kronos 并行）。

    --streaming 时启用流式模式：数据拉取与模型推理重叠执行，
    总耗时从 T_fetch + T_compute 缩短为 max(T_fetch, T_compute)。

    --auto-universe 时自动从全市场 A 股中筛选候选股票，
    忽略 --tickers / --stock-file 参数。
    """
    _load_env()

    from trade_krono_cli.pipeline import QuantPipeline

    # 解析过滤配置（CLI 参数优先，其次 fallback 到 Settings 默认）
    from trade_krono_cli.pipeline.config_loader import _parse_comma_list, _parse_range

    mc_range = _parse_range(market_cap_range) if market_cap_range else None
    ind_whitelist = _parse_comma_list(industry_whitelist) if industry_whitelist else None
    ind_blacklist = _parse_comma_list(industry_blacklist) if industry_blacklist else None
    pe_r = _parse_range(pe_range) if pe_range else None
    pb_r = _parse_range(pb_range) if pb_range else None

    # ── 股票列表来源 ───────────────────────────────────────────────
    if auto_universe:
        console.print("[bold cyan]🔍 自动发现全市场 A 股 ...[/bold cyan]")
        from trade_krono_cli.configs.filters import FilterConfig
        from trade_krono_cli.universe.engine import UniverseEngine

        fc_overrides: dict = {}
        if mc_range:
            fc_overrides["market_cap_range"] = mc_range
        if market_cap_min is not None:
            fc_overrides["market_cap_min"] = market_cap_min
        if min_volume is not None:
            # CLI 接受"股"，内部转换为"手"（1手=100股）
            fc_overrides["min_volume"] = min_volume / 100
        if ind_whitelist:
            fc_overrides["industry_whitelist"] = ind_whitelist
        if ind_blacklist:
            fc_overrides["industry_blacklist"] = ind_blacklist
        if not exclude_st:
            fc_overrides["exclude_st"] = False

        fc = FilterConfig(**fc_overrides)
        engine = UniverseEngine.from_config(fc, universe_source=universe_source)
        console.print(
            f"   数据源: {universe_source} | "
            f"过滤: exclude_st={fc.exclude_st}, "
            f"低价阈值={fc.low_price_threshold}元, "
            f"市值≥{fc.market_cap_min or '不限'}亿, "
            f"成交量≥{int(fc.min_volume * 100 if fc.min_volume else 0):,}股",
        )
        tk_list = engine.run(eval_date=date)
        if not tk_list:
            console.print("[red]❌ 全市场筛选后无候选股票[/red]")
            raise typer.Exit(1)
        console.print(f"[green]✅ 筛选完成: {len(tk_list)} 只股票进入候选池[/green]")
        if max_tickers is not None and max_tickers < len(tk_list):
            tk_list = tk_list[:max_tickers]
            console.print(
                f"[dim]   受 --max-tickers={max_tickers} 限制，取前 {len(tk_list)} 只[/dim]",
            )
    else:
        tk_list = _load_tickers(tickers, stock_file)
        if not tk_list:
            console.print("[red]❌ 股票列表为空（请通过 --tickers 或 --stock-file 提供）[/red]")
            raise typer.Exit(1)

    signals_tuple = tuple(x.strip().upper() for x in signals.split(","))

    # 构建覆盖配置的 override dict
    filter_overrides: dict = {}
    if mc_range:
        filter_overrides["market_cap_range"] = mc_range
    if market_cap_min is not None:
        filter_overrides["market_cap_min"] = market_cap_min
    if min_volume is not None:
        # CLI 接受"股"，内部转换为"手"（1手=100股）
        filter_overrides["min_volume"] = min_volume / 100
    if ind_whitelist:
        filter_overrides["industry_whitelist"] = ind_whitelist
    if ind_blacklist:
        filter_overrides["industry_blacklist"] = ind_blacklist
    if pe_r:
        filter_overrides["pe_range"] = pe_r
    if pb_r:
        filter_overrides["pb_range"] = pb_r
    if max_risk_score is not None:
        filter_overrides["max_risk_score"] = max_risk_score
    if min_volume_ratio is not None:
        filter_overrides["min_volume_ratio"] = min_volume_ratio
    if not exclude_st:
        filter_overrides["exclude_st"] = False

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

    console.print(f"[bold green]🚀 启动流水线[/bold green] {len(tk_list)} 只 → {date}")

    def _progress(stage: str, cur: int, total: int) -> None:
        console.print(f"  [cyan]{stage}[/cyan] [{cur}/{total}]")

    project_root = _PROJECT_ROOT
    json_out_p = _sanitize_path(json_out, "JSON", project_root)
    html_out_p = _sanitize_path(html_out, "HTML", project_root)

    all_overrides = {**filter_overrides, **strategy_overrides}

    # 重试策略覆盖
    retry_overrides = _build_retry_overrides(
        max_retries=max_retries,
        base_delay=base_delay,
        no_jitter=no_jitter,
        no_rate_limit_backoff=no_rate_limit_backoff,
    )
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
            if config_file
            else PipelineConfig.default().override(**all_overrides)
        )
        if all_overrides
        else (PipelineConfig.load(config_file) if config_file else None),
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
    tickers: str | None = typer.Option(None, "--tickers", "-t"),
    config: str | None = typer.Option(None, "--config", "-c"),
    date: str = typer.Option(..., "--date", "-d"),
    output: str = typer.Option("outputs/ta_result.json", "--output", "-o"),
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
    degrade_mode: str = typer.Option(
        "strict",
        "--degrade-mode",
        help="降级策略：strict / ta_only_on_kronos_fail / ta_cache_fallback",
        rich_help_panel="降级策略",
    ),
    ta_cache_fallback: bool = typer.Option(
        False,
        "--ta-cache-fallback",
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

    project_root = _PROJECT_ROOT
    output_p = _sanitize_path(output, "TA输出", project_root)

    # 构建重试策略
    retry_overrides = _build_retry_overrides(
        max_retries=max_retries, base_delay=base_delay, no_jitter=no_jitter,
    )
    cfg = PipelineConfig.default().override(**retry_overrides) if retry_overrides else None
    degrade_overrides = _build_degrade_overrides(degrade_mode, ta_cache_fallback)
    if degrade_overrides:
        cfg = (cfg or PipelineConfig.default()).override(**degrade_overrides)
    pipeline = QuantPipeline(config=cfg)
    results = pipeline.run_ta_only(tk_list, date, output=str(output_p))

    console.print(f"[green]✅ TA 分析完成 → {output}[/green]")
    console.print(f"   成功: {sum(1 for r in results if r.error is None)}/{len(results)}")


# ═══════════════════════════════════════════════════════
# kronos — 仅 Kronos 预测
# ═══════════════════════════════════════════════════════


def kronos(
    tickers: str | None = typer.Option(None, "--tickers", "-t"),
    date: str = typer.Option(..., "--date", "-d"),
    pred_len: int = typer.Option(30, "--pred-len"),
    lookback: int = typer.Option(400, "--lookback"),
    sample_count: int = typer.Option(
        None, "--sample-count", help="Kronos 采样次数（默认 5，设 1 为快速模式）",
    ),
    config_file: str | None = typer.Option(
        None, "--config", "-c", help="Pipeline 配置文件路径（YAML/JSON，覆盖默认配置）",
    ),
    output: str = typer.Option("outputs/kronos_result.json", "--output", "-o"),
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
    degrade_mode: str = typer.Option(
        "strict",
        "--degrade-mode",
        help="降级策略：strict / ta_only_on_kronos_fail / ta_cache_fallback",
        rich_help_panel="降级策略",
    ),
    ta_cache_fallback: bool = typer.Option(
        False,
        "--ta-cache-fallback",
        help="启用 TA 缓存回退（需配合 --degrade-mode ta_cache_fallback）",
        rich_help_panel="降级策略",
    ),
) -> None:
    """仅运行 Kronos 批量预测。"""
    _load_env()

    from trade_krono_cli.pipeline import QuantPipeline

    tk_list = _load_tickers(tickers, config_file)
    if not tk_list:
        console.print("[red]❌ 股票列表为空[/red]")
        raise typer.Exit(1)

    project_root = _PROJECT_ROOT
    output_p = _sanitize_path(output, "Kronos输出", project_root)

    # 构建重试策略
    retry_overrides = _build_retry_overrides(
        max_retries=max_retries, base_delay=base_delay, no_jitter=no_jitter,
    )
    cfg = PipelineConfig.default().override(**retry_overrides) if retry_overrides else None
    degrade_overrides = _build_degrade_overrides(degrade_mode, ta_cache_fallback)
    if degrade_overrides:
        cfg = (cfg or PipelineConfig.default()).override(**degrade_overrides)
    pipeline = QuantPipeline(
        sample_count=sample_count,
        config=cfg or (PipelineConfig.load(config_file) if config_file else None),
    )
    results = pipeline.run_kronos_only(tk_list, date, output=str(output_p))

    console.print(f"[green]✅ Kronos 预测完成 → {output}[/green]")
    console.print(f"   成功: {sum(1 for r in results if r.error is None)}/{len(results)}")
