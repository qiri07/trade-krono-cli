"""CLI 核心辅助函数 — 配置加载、路径校验、股票列表解析。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import typer
from rich.console import Console

from trade_krono_cli.config import Settings, get_settings
from trade_krono_cli.logger import setup_logger

console: Console = Console()

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


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
