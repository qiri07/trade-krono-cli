#!/usr/bin/env python3
"""
端到端投研流水线：
  1. 数据同步（sync-whitelist）
  2. 巴菲特六闸门筛选 → 保存至带日期文件
  3. 对通过筛选的股票运行 TA 分析 + Kronos 预测
  4. 推送结果至飞书群
"""

from __future__ import annotations

import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import typer
from rich.console import Console

console = Console()

APP = typer.Typer(help="端到端投研流水线")
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _run(cmd: list[str], desc: str, check: bool = True) -> subprocess.CompletedProcess:
    console.print(f"\n[bold cyan]▶ {desc}[/bold cyan]")
    console.print(f"  命令: {' '.join(cmd)}")
    start = time.time()
    result = subprocess.run(cmd, cwd=str(PROJECT_ROOT), capture_output=True, text=True)
    elapsed = time.time() - start
    if result.stdout:
        for line in result.stdout.strip().splitlines():
            console.print(f"  {line}")
    if result.stderr and not result.returncode:
        for line in result.stderr.strip().splitlines():
            console.print(f"  [dim]{line}[/dim]")
    if check and result.returncode != 0:
        console.print(f"[red]❌ {desc} 失败（exit={result.returncode}，耗时 {elapsed:.1f}s）[/red]")
        if result.stderr:
            console.print(f"[red]  stderr: {result.stderr[-500:]}[/red]")
        sys.exit(result.returncode)
    console.print(f"  ✅ 完成（{elapsed:.1f}s）")
    return result


@APP.callback()
def _callback() -> None:
    """加载环境变量。"""
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env", override=False)


@APP.command("full-pipeline")
def full_pipeline(
    date: str = typer.Option("today", "--date", "-d", help="分析日期 YYYY-MM-DD 或 today"),
    no_sync: bool = typer.Option(False, "--no-sync", help="跳过数据同步步骤"),
    max_tickers: int | None = typer.Option(
        None, "--max-tickers", help="限制最大处理股票数（用于测试）"
    ),
    skip_kronos: bool = typer.Option(
        False, "--skip-kronos", help="仅运行 TA 分析，跳过 Kronos 预测"
    ),
    send_feishu_flag: bool = typer.Option(
        True, "--feishu/--no-feishu", help="是否推送飞书（默认推送）"
    ),
) -> None:
    """端到端投研流水线：数据同步 → 巴菲特筛选 → TA+Kronos分析 → 飞书推送。"""

    today_str = datetime.now().strftime("%Y%m%d")
    report_date = datetime.now().strftime("%Y-%m-%d") if date == "today" else date
    buffett_file = Path("outputs/results/buffett_screen_" + today_str + ".txt")
    output_dir = PROJECT_ROOT / "outputs" / "results"
    output_dir.mkdir(parents=True, exist_ok=True)

    bar = "━" * 60

    console.print(f"\n{bar}")
    console.print(f"  🚀 端到端投研流水线  {report_date}")
    console.print(f"{bar}\n")

    # ── Step 1: 数据同步 ───────────────────────────────────────────────
    if not no_sync:
        _run(
            ["uv", "run", "trade-krono-cli", "sync-whitelist"],
            "Step 1: 同步白名单 K 线缓存",
        )

    # ── Step 2: 巴菲特筛选 ─────────────────────────────────────────────
    console.print("\n[bold cyan]▶ Step 2: 巴菲特六闸门筛选[/bold cyan]")
    screen_result = subprocess.run(
        ["uv", "run", "python", "tests/buffett_screen_parallel.py"],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
    )
    screen_out = screen_result.stdout
    if screen_result.stderr:
        screen_out += "\n" + screen_result.stderr

    # 解析输出，提取通过筛选的股票代码
    passed_tickers: list[str] = []
    for line in screen_out.splitlines():
        # 格式: "  000001  平安银行: PE=xx PB=xx ..."
        # 或通过表格行: "000001   平安银行           xx.x   x.xx ..."
        stripped = line.strip()
        if not stripped or not stripped[0].isdigit():
            continue
        parts = stripped.split()
        if len(parts) >= 2 and parts[0].isdigit() and len(parts[0]) == 6:
            code = parts[0]
            if code not in passed_tickers:
                passed_tickers.append(code)

    # 同时从终端输出中提取
    for line in screen_out.splitlines():
        if "✅" in line:
            # 格式: "  ✅ 000001 平安银行: PE=..."
            tokens = line.split()
            for t in tokens:
                if t.isdigit() and len(t) == 6:
                    if t not in passed_tickers:
                        passed_tickers.append(t)
                    break

    # 写文件
    with open(PROJECT_ROOT / buffett_file, "w", encoding="utf-8") as f:
        f.write(screen_out)

    console.print(f"  ✅ 筛选完成，通过 {len(passed_tickers)} 只，结果已写入: {buffett_file}")

    if not passed_tickers:
        console.print("[yellow]⚠️  没有股票通过五闸门，流水线终止[/yellow]")
        sys.exit(0)

    # ── Step 3: TA + Kronos 分析 ───────────────────────────────────────
    ticker_arg = ",".join(passed_tickers)
    if max_tickers is not None and max_tickers < len(passed_tickers):
        ticker_arg = ",".join(passed_tickers[:max_tickers])
        console.print(f"  [dim]受 --max-tickers={max_tickers} 限制，处理前 {max_tickers} 只[/dim]")

    console.print(
        f"\n[bold cyan]▶ Step 3: TA 分析 + Kronos 预测 ({len(passed_tickers)} 只)[/bold cyan]"
    )
    run_cmd = [
        "uv",
        "run",
        "trade-krono-cli",
        "run",
        "--tickers",
        ticker_arg,
        "--date",
        report_date,
        "--json",
        str(PROJECT_ROOT / "outputs/results/pipeline_report.json"),
        "--html",
        str(PROJECT_ROOT / "outputs/results/pipeline_report.html"),
    ]
    if skip_kronos:
        run_cmd.append("--skip-kronos")

    _run(run_cmd, "Step 3: 股票分析+预测")

    # ── Step 4: 飞书推送 ────────────────────────────────────────────────
    if send_feishu_flag:
        console.print("\n[bold cyan]▶ Step 4: 推送飞书通知[/bold cyan]")
        _send_feishu_notification(
            date=report_date,
            passed_count=len(passed_tickers),
            tickers=passed_tickers,
            buffett_file=str(buffett_file),
        )

    console.print(f"\n{bar}")
    console.print("  ✅ 全流程完成！")
    console.print(f"  筛选结果: {buffett_file}")
    console.print("  分析报告: outputs/results/pipeline_report.json / .html")
    console.print(f"{bar}\n")


def _send_feishu_notification(
    date: str,
    passed_count: int,
    tickers: list[str],
    buffett_file: str,
) -> None:
    """构建飞书消息并发送。"""
    from trade_krono_cli.notify import send_feishu

    lines: list[str] = []
    lines.append(f"📊 **投研流水线报告** — {date}")
    lines.append("")
    lines.append(f"✅ 巴菲特六闸门筛选通过：**{passed_count} 只**")
    lines.append("")
    lines.append("**通过股票列表：**")
    for t in tickers:
        lines.append(f"• {t}")
    lines.append("")
    lines.append(f"📁 筛选详情: {buffett_file}")
    lines.append("📈 分析报告: outputs/results/pipeline_report.{json,html}")
    lines.append("")
    lines.append("_自动推送于 " + datetime.now().strftime("%Y-%m-%d %H:%M"))

    content = "\n".join(lines)
    send_feishu(content=content)


if __name__ == "__main__":
    APP()
