"""CLI 命令 — kronos：仅 Kronos 预测。"""

from __future__ import annotations

import typer
from rich.console import Console

from trade_krono_cli.cli_commands._core_helpers import (
    _PROJECT_ROOT,
    _build_degrade_overrides,
    _build_retry_overrides,
    _load_env,
    _load_tickers,
    _sanitize_path,
)
from trade_krono_cli.pipeline_config import PipelineConfig

console: Console = Console()


def kronos(
    tickers: str | None = typer.Option(None, "--tickers", "-t"),
    date: str = typer.Option(..., "--date", "-d"),
    pred_len: int = typer.Option(30, "--pred-len"),
    lookback: int = typer.Option(400, "--lookback"),
    sample_count: int = typer.Option(
        None,
        "--sample-count",
        help="Kronos 采样次数（默认 5，设 1 为快速模式）",
    ),
    config_file: str | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Pipeline 配置文件路径（YAML/JSON，覆盖默认配置）",
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
        max_retries=max_retries,
        base_delay=base_delay,
        no_jitter=no_jitter,
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
