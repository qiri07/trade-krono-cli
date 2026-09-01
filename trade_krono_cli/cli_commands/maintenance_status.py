"""状态查看命令 — status：密钥、缓存、模型配置 + 健康检查。"""

from __future__ import annotations

from trade_krono_cli.cli_commands.core import _load_env

console = None  # lazily imported below


def status() -> None:
    """查看系统状态：密钥、缓存、模型配置 + 健康检查。"""
    _load_env()

    from rich.console import Console
    from rich.table import Table

    from trade_krono_cli.cache import get_cache
    from trade_krono_cli.health import health_summary, print_health_report
    from trade_krono_cli.research_db import get_research
    from trade_krono_cli.security import KeyVault

    global console
    console = Console()

    vault = KeyVault()
    status_map = vault.validate()

    table = Table(title="🔐 系统状态", header_style="bold cyan")
    for col in ("项目", "状态"):
        table.add_column(col)
    table.add_row("项目根目录", str(_load_env()[0].project_root))
    table.add_row("结果目录", str(_load_env()[0].results_dir))
    table.add_row("缓存目录", str(_load_env()[0].cache_dir))
    table.add_row("LLM 供应商", _load_env()[0].llm_provider)
    table.add_row("Deep 模型", _load_env()[0].deep_think_llm)
    table.add_row("Quick 模型", _load_env()[0].quick_think_llm)
    table.add_row("Kronos 模型", _load_env()[0].kronos_model)
    table.add_row("Kronos 设备", _load_env()[0].kronos_device)
    for k, v in status_map.items():
        table.add_row(k, "✅ 已配置" if v else "⚠️ 缺失")
    console.print(table)

    try:
        cache_stats = get_cache().stats()
        console.print(f"[dim]缓存: {cache_stats}[/dim]")
    except Exception as e:
        console.print(f"[dim]缓存统计不可用: {e}[/dim]")

    # ── 健康检查 ────────────────────────────────────────────────────────────
    settings = _load_env()[0]
    results = health_summary(settings)
    print_health_report(results)

    try:
        research = get_research()
        res_stats = research.stats()
        console.print(f"[dim]研究数据库: {res_stats}[/dim]")
        jobs = research.list_jobs(limit=5)
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
