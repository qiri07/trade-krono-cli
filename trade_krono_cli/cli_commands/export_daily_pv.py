"""export-daily-pv 命令 — 将 trade-krono-cli 缓存导出为 RD-Agent daily_pv 格式。"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from trade_krono_cli.cli_commands.core import _load_env

console = Console()


def export_daily_pv(
    rdagent_workspace: str = typer.Option(
        "git_ignore_folder/factor_implementation_source_data",
        "--rdagent-workspace",
        "-w",
        help="RD-Agent 数据目录（相对于 workspace 根目录），默认 git_ignore_folder/factor_implementation_source_data",
    ),
    debug_stocks: int = typer.Option(
        100,
        "--debug-stocks",
        "-d",
        help="Debug 数据集包含的股票数量（默认 100）",
    ),
    no_h5: bool = typer.Option(
        False,
        "--no-h5",
        help="跳过 HDF5 生成（仅输出 parquet，更快）",
    ),
) -> None:
    """将 trade-krono-cli 缓存导出为 RD-Agent daily_pv 格式（parquet + h5）。

    导出的文件可直接被 RD-Agent 读取，无需额外转换。

    示例：
      trade-krono-cli export-daily-pv                        # 导出到默认位置
      trade-krono-cli export-daily-pv --no-h5                # 仅生成 parquet
      trade-krono-cli export-daily-pv -w /path/to/data       # 指定输出目录
      trade-krono-cli export-daily-pv -d 50                  # debug 集仅 50 只股票
    """
    _load_env()

    from trade_krono_cli.cache import get_cache

    cache = get_cache()

    # ── 解析输出路径 ─────────────────────────────────────────────────────
    base_dir = Path(rdagent_workspace)
    if not base_dir.is_absolute():
        # RD-Agent 工作空间是 trade-krono-cli 的同级目录
        base_dir = Path(__file__).resolve().parents[3] / "RD-Agent-Work" / base_dir

    main_dir = base_dir
    debug_dir = base_dir.parent / (base_dir.name + "_debug")

    parquet_main = main_dir / "daily_pv.parquet"
    h5_main = main_dir / "daily_pv.h5"
    parquet_debug = debug_dir / "daily_pv.parquet"

    console.print("[bold cyan]📦 开始导出 daily_pv 数据[/bold cyan]")
    console.print(f"   主数据: {parquet_main}")
    console.print(f"   Debug : {parquet_debug}  （{debug_stocks} 只股票）")
    if no_h5:
        console.print("   h5    : 跳过（--no-h5）")
    else:
        console.print(f"   h5    : {h5_main}")

    # ── 执行导出 ──────────────────────────────────────────────────────────
    extra_kwargs: dict = {}
    if not no_h5:
        extra_kwargs["h5_path"] = str(h5_main)
    if debug_stocks > 0:
        extra_kwargs["debug_insts"] = debug_stocks

    stats = cache.export_daily_pv(
        parquet_path=str(parquet_main),
        **extra_kwargs,
    )

    # ── 输出汇总 ──────────────────────────────────────────────────────────
    console.print()
    console.print("[bold green]✅ 导出完成[/bold green]")
    console.print(f"   股票数 : {stats['stocks']:,}")
    console.print(f"   数据行 : {stats['rows']:,}")
    console.print(f"   日期范围: {stats['date_min']} ~ {stats['date_max']}")
    console.print(f"   parquet: {stats['parquet_path']}")
    if stats.get("h5_path"):
        console.print(f"   h5     : {stats['h5_path']} ({stats.get('h5_size_mb', '?')} MB)")
    if stats.get("debug_rows"):
        console.print(f"   debug  : {stats['debug_rows']:,} 行, {stats['debug_stocks']} 只")
    console.print()
