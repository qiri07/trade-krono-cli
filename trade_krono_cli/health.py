"""
健康检查 — 轻量级系统诊断，不触发重负载操作（不加载模型、不发 API 请求）。

每个 check_* 函数返回 HealthResult，包含状态和描述信息。
health_summary() 汇总所有检查并打印报告。
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from trade_krono_cli.config import Settings


@dataclass
class HealthResult:
    """单项健康检查结果。"""

    name: str
    ok: bool
    detail: str  # "OK" / "缺失" / 错误原因等


# ── 检查函数 ──────────────────────────────────────────────────────────────────


def check_llm_api() -> HealthResult:
    """
    检查 LLM API Key 可用性。

    仅验证环境变量存在，不发起网络请求。
    已配置的 provider 用 ✅，缺失的用 ⚠️，无配置用 ❌。
    """
    from trade_krono_cli.security import KeyVault

    vault = KeyVault()
    configured = vault.validate()

    if not configured:
        return HealthResult("LLM API", False, "未配置任何 provider")

    parts = []
    all_ok = True
    for provider, has_key in configured.items():
        if has_key:
            parts.append(f"✅ {provider}")
        else:
            all_ok = False
            parts.append(f"⚠️  {provider}")

    if not any(p.startswith("✅") for p in parts):
        return HealthResult("LLM API", False, "未配置任何 provider")

    return HealthResult("LLM API", all_ok, ", ".join(parts))


def check_kronos_import() -> HealthResult:
    """
    检查 Kronos 依赖是否可导入（轻量：仅 import，不加载模型）。

    验证适配器能否加载，以及 torch 是否可用（可选）。
    """
    try:
        from trade_krono_cli.adapters import KronosAdapterImpl

        KronosAdapterImpl()
    except ImportError as e:
        return HealthResult("Kronos", False, f"无法导入适配器: {e}")

    try:
        import torch

        if torch.cuda.is_available():
            torch_detail = f"GPU ({torch.cuda.device_count()} 卡)"
        else:
            torch_detail = "CPU"
    except ImportError:
        torch_detail = "torch 未安装（使用 CPU）"

    return HealthResult(
        "Kronos",
        True,
        f"导入正常，推理引擎={torch_detail}",
    )


def check_database(db_path: Path) -> HealthResult:
    """
    检查 Research 数据库连接是否正常。

    执行 PRAGMA integrity_check + SELECT 1，失败则返回错误。
    """
    try:
        conn = sqlite3.connect(str(db_path), timeout=5)
        with conn:
            conn.execute("PRAGMA integrity_check")
            conn.execute("SELECT 1 LIMIT 0")
        return HealthResult("数据库", True, f"✅ {db_path.name} OK")
    except (sqlite3.Error, OSError) as e:
        return HealthResult("数据库", False, f"❌ {type(e).__name__}: {str(e)[:80]}")


def check_disk_space(path: Path, min_gb: float = 0.5) -> HealthResult:
    """
    检查磁盘剩余空间是否充足（默认至少 0.5 GB）。

    检查 path 所在文件系统的可用空间。
    """
    try:
        stat = os.statvfs(str(path))
        free_bytes = stat.f_bavail * stat.f_frsize
        free_gb = free_bytes / (1024**3)
        if free_gb < min_gb:
            return HealthResult(
                "磁盘空间",
                False,
                f"❌ 可用空间 {free_gb:.2f} GB < {min_gb} GB 阈值",
            )
        return HealthResult(
            "磁盘空间",
            True,
            f"✅ 可用 {free_gb:.1f} GB",
        )
    except OSError as e:
        return HealthResult("磁盘空间", False, f"❌ 无法检测: {e}")


# ── 汇总 ──────────────────────────────────────────────────────────────────────


def health_summary(
    settings: Settings,
) -> list[HealthResult]:
    """
    运行所有健康检查，按顺序返回结果列表。

    Parameters
    ----------
    settings : Settings
        当前全局配置，用于获取数据库路径和缓存目录。
    """
    # 延迟导入避免循环依赖
    from trade_krono_cli.research_db import get_research

    results: list[HealthResult] = []
    results.append(check_llm_api())
    results.append(check_kronos_import())

    # 数据库
    try:
        research = get_research()
        db_path = research._db_path
    except Exception:
        db_path = settings.cache_dir / "pipeline_cache.db"
    results.append(check_database(db_path))

    # 磁盘空间
    results.append(check_disk_space(settings.cache_dir))

    return results


def print_health_report(results: list[HealthResult]) -> bool:
    """
    打印健康检查报告到控制台，返回是否有失败项。

    格式：
      🔍 健康检查
      ├─ LLM API         ✅ deepseek, openai
      ├─ Kronos          ✅ 导入正常，推理引擎=CPU
      ├─ 数据库          ✅ pipeline_cache.db OK
      └─ 磁盘空间        ✅ 可用 42.3 GB
    """
    from rich.console import Console

    console = Console()
    console.print()
    console.print("[bold cyan]🔍 健康检查[/bold cyan]")

    all_ok = True
    for i, r in enumerate(results):
        prefix = "├─ " if i < len(results) - 1 else "└─ "
        icon = "✅" if r.ok else "❌"
        console.print(f"  {prefix}[bold]{r.name}[/bold]  {icon} {r.detail}")
        if not r.ok:
            all_ok = False

    console.print()
    status = "[green]全部通过[/green]" if all_ok else "[red]存在问题，请检查[/red]"
    console.print(f"  整体: {status}")
    console.print()

    return all_ok
