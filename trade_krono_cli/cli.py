"""
trade-krono-cli CLI 入口 — Typer 实现。

支持命令：
  run        一键运行（TA + Kronos 并行）
  ta         仅 TradingAgents 分析
  kronos     仅 Kronos 预测
  status     查看系统状态
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
):
    """🔥 一键运行完整流水线（TA 与 Kronos 并行）。"""
    _load_env()

    from trade_krono_cli.pipeline import QuantPipeline

    tk_list = _load_tickers(tickers, config)
    if not tk_list:
        console.print("[red]❌ 股票列表为空（请通过 --tickers 或 --config 提供）[/red]")
        raise typer.Exit(1)

    signals_tuple = tuple(x.strip().upper() for x in signals.split(","))

    console.print(f"[bold green]🚀 启动流水线[/bold green] {len(tk_list)} 只 → {date}")

    # 进度回调
    def _progress(stage: str, cur: int, total: int) -> None:
        console.print(f"  [cyan]{stage}[/cyan] [{cur}/{total}]")

    pipeline = QuantPipeline(
        skip_kronos=skip_kronos,
        min_confidence=min_confidence,
        allowed_signals=signals_tuple,
    )

    merged = pipeline.run_parallel(
        tickers=tk_list,
        date=date,
        output_json=json_out,
        output_html=html_out,
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

    pipeline = QuantPipeline()
    results = pipeline.run_ta_only(tk_list, date, output=output)

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
    output: str = typer.Option("outputs/kronos_result.json", "--output", "-o"),
):
    """仅运行 Kronos 批量预测。"""
    _load_env()

    from trade_krono_cli.pipeline import QuantPipeline

    tk_list = _load_tickers(tickers, config)
    if not tk_list:
        console.print("[red]❌ 股票列表为空[/red]")
        raise typer.Exit(1)

    pipeline = QuantPipeline()
    results = pipeline.run_kronos_only(tk_list, date, output=output)

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
        stats = get_cache().stats()
        console.print(f"[dim]缓存: {stats}[/dim]")
    except Exception as e:
        console.print(f"[dim]缓存统计不可用: {e}[/dim]")


# ═══════════════════════════════════════════════════════
# clear-cache — 清除缓存
# ═══════════════════════════════════════════════════════

@app.command()
def clear_cache():
    """清除所有缓存（K线/TA/Kronos）。"""
    _load_env()

    from trade_krono_cli.cache import get_cache
    n = get_cache().clear_all()
    console.print(f"[yellow]🧹 已清除 {n} 条缓存[/yellow]")


# ═══════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════

def main():
    app()


if __name__ == "__main__":
    main()
