"""
报告输出 — JSON / HTML / 控制台表格。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger
from rich.console import Console
from rich.table import Table

console = Console()


# ═══════════════════════════════════════════════════════
# JSON 报告
# ═══════════════════════════════════════════════════════

def save_json(merged: list[dict], path: str) -> str:
    """保存 JSON 报告，截断 forecast_dict 避免文件过大。"""
    clean = []
    for m in merged:
        c = dict(m)
        fd = c.get("forecast_dict")
        if fd:
            c["forecast_dict"] = {
                "timestamps": fd.get("timestamps", [])[:5],
                "close": fd.get("close", [])[:5],
                "note": f"截断显示，共 {len(fd.get('close', []))} 个预测点",
            }
        clean.append(c)

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(clean, f, ensure_ascii=False, indent=2)
    logger.info(f"💾 JSON 报告已保存: {path}")
    return path


# ═══════════════════════════════════════════════════════
# HTML 报告
# ═══════════════════════════════════════════════════════

def save_html(merged: list[dict], path: str, date: str) -> str:
    """生成 HTML 报告。"""
    rows = []
    for i, m in enumerate(merged, 1):
        score = m.get("composite_score") or 0
        color = "#28a745" if score >= 70 else "#ffc107" if score >= 50 else "#dc3545"
        rows.append(f"""
            <tr>
              <td>{i}</td>
              <td><b>{m['ticker']}</b></td>
              <td>{m.get('ta_signal') or '-'}</td>
              <td>{m.get('ta_confidence') or '-'}</td>
              <td>{m.get('kronos_direction') or '-'}</td>
              <td>{m.get('kronos_change_pct') or 0:.2f}%</td>
              <td>{m.get('kronos_last_close') or '-'}</td>
              <td>{m.get('kronos_pred_close') or '-'}</td>
              <td style="color:{color};font-weight:bold">{score:.1f}</td>
            </tr>""")

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    html = f"""<!DOCTYPE html>
<html lang="zh"><head>
<meta charset="utf-8">
<title>trade-krono-cli 报告 {date}</title>
<style>
body {{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;margin:20px;background:#f8f9fa}}
h1 {{color:#333}} table {{border-collapse:collapse;width:100%;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.1)}}
th {{background:#343a40;color:#fff;padding:10px;text-align:left}}
td {{padding:8px 10px;border-bottom:1px solid #eee}}
tr:hover {{background:#f1f3f5}}
.meta {{color:#666;font-size:.9em;margin-bottom:15px}}
.signal-BUY {{color:#28a745;font-weight:bold}}
.signal-SELL {{color:#dc3545;font-weight:bold}}
.signal-HOLD {{color:#ffc107;font-weight:bold}}
</style>
</head><body>
<h1>📊 trade-krono-cli 投研报告</h1>
<p class="meta">分析日期: {date} | 生成时间: {now_str} | 共 {len(merged)} 只</p>
<table>
<tr>
  <th>排名</th><th>代码</th><th>TA信号</th><th>置信度</th>
  <th>Kronos方向</th><th>预期涨幅%</th><th>现价</th><th>预测价</th><th>综合分</th>
</tr>
{''.join(rows)}
</table>
</body></html>"""

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    logger.info(f"💾 HTML 报告已保存: {path}")
    return path


# ═══════════════════════════════════════════════════════
# 控制台输出
# ═══════════════════════════════════════════════════════

def print_table(merged: list[dict]) -> None:
    """在控制台输出富文本表格。"""
    table = Table(title="📊 综合排名", header_style="bold magenta")
    for col in ("排名", "代码", "TA信号", "置信度", "Kronos方向", "预期涨幅%", "综合分"):
        table.add_column(col, justify="right" if col != "代码" else "left")

    for m in merged[:20]:
        table.add_row(
            str(m.get("rank", "-")),
            m.get("ticker", "-"),
            str(m.get("ta_signal") or "-"),
            str(m.get("ta_confidence") or "-"),
            str(m.get("kronos_direction") or "-"),
            f"{m.get('kronos_change_pct') or 0:.2f}",
            f"[bold]{m.get('composite_score') or '-'}[/bold]",
        )
    console.print(table)


def print_summary(merged: list[dict], date: str) -> None:
    """打印简要摘要。"""
    console.print(f"\n[bold cyan]📅 分析日期: {date}[/bold cyan]")
    console.print(f"[bold cyan]📈 共分析 {len(merged)} 只股票[/bold cyan]\n")

    # 最佳
    if merged:
        best = merged[0]
        console.print(
            f"[bold green]🥇 最佳推荐: {best['ticker']} "
            f"(TA={best.get('ta_signal')}  Kronos={best.get('kronos_direction')} "
            f"综合分={best.get('composite_score')})[/bold green]"
        )

    # BUY 信号汇总
    buys = [m for m in merged if m.get("ta_signal") == "BUY"]
    if buys:
        console.print(f"[green]💚 BUY 信号: {len(buys)} 只[/green]")
        for m in buys[:5]:
            console.print(
                f"   • {m['ticker']}: 置信度={m.get('ta_confidence')} "
                f"Kronos预期={m.get('kronos_change_pct') or 0:.2f}%"
            )

    console.print()
