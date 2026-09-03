"""历史记录命令 — history：查看历史分析记录。"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from trade_krono_cli.cli_commands.core import _load_env

console = Console()


def history(
    ticker: str | None = typer.Option(
        None,
        "--ticker",
        "-t",
        help="指定股票代码，查看该股票的历史分析记录",
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
