"""
pipeline.reporter — 报告输出（JSON / HTML / 控制台表格）。

原 trade_krono_cli.report 收敛至此。
所有函数以 _report 后缀命名，保持与 orchestrator 的调用一致；
旧名称作为别名保留，避免测试导入失效。
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
# 降级标记 helpers
# ═══════════════════════════════════════════════════════

def _degradation_badge(dmg: Optional[str]) -> tuple[str, str]:
    """返回 (html_badge, rich_console_str) 降级标记。

    html_badge   — HTML <span> 片段，用于 save_html_report
    rich_console — Rich console markup 字符串，用于 print_results_table / summary
    """
    if dmg == "kronos_degraded":
        return (
            '<span style="background:#fff3cd;color:#856404;padding:2px 6px;border-radius:3px;font-size:.8em;margin-left:6px">⚠ TA-only</span>',
            "[yellow]⚠ TA-only[/yellow]",
        )
    if dmg == "ta_cache_fallback":
        return (
            '<span style="background:#d1ecf1;color:#0c5460;padding:2px 6px;border-radius:3px;font-size:.8em;margin-left:6px">📦 缓存TA</span>',
            "[cyan]📦 缓存TA[/cyan]",
        )
    return ("", "—")


# ═══════════════════════════════════════════════════════
# JSON 报告
# ═══════════════════════════════════════════════════════

def save_json_report(merged: list[dict], path: str) -> str:
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
        # 确保 degradation_mode 字段存在
        if "degradation_mode" not in c:
            c["degradation_mode"] = None
        clean.append(c)

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(clean, f, ensure_ascii=False, indent=2)
    logger.info(f"💾 JSON 报告已保存: {path}")
    return path


# save_json 别名（向后兼容）
save_json = save_json_report


# ═══════════════════════════════════════════════════════
# HTML 报告
# ═══════════════════════════════════════════════════════

def save_html_report(merged: list[dict], path: str, date: str) -> str:
    """生成 HTML 报告。"""
    rows = []
    for i, m in enumerate(merged, 1):
        score = m.get("composite_score") or 0
        color = "#28a745" if score >= 70 else "#ffc107" if score >= 50 else "#dc3545"
        pu = m.get("kronos_prediction_uncertainty") or {}
        cs = pu.get("confidence_score")
        cs_str = f"{cs:.1f}" if cs is not None else "-"
        pd_val = pu.get("path_dispersion")
        pd_str = f"{pd_val:.4f}" if pd_val is not None else "N/A"
        dc = pu.get("direction_confidence")
        dc_str = f"{dc:.3f}" if dc is not None else "-"
        dmg = m.get("degradation_mode")
        badge, _ = _degradation_badge(dmg)
        rows.append(f"""
            <tr>
              <td>{i}</td>
              <td><b>{m['ticker']}</b>{badge}</td>
              <td>{m.get('ta_signal') or '-'}</td>
              <td>{m.get('ta_confidence') or '-'}</td>
              <td>{m.get('kronos_direction') or '-'}</td>
              <td>{m.get('kronos_change_pct') or 0:.2f}%</td>
              <td>{m.get('kronos_last_close') or '-'}</td>
              <td>{m.get('kronos_pred_close') or '-'}</td>
              <td title="方向置信度={dc_str}<br>路径分散={pd_str}<br>综合置信={cs_str}">{cs_str}</td>
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
  <th>Kronos方向</th><th>预期涨幅%</th><th>现价</th><th>预测价</th>
  <th title="Kronos综合置信度（0-100，含方向+路径分散）">置信度</th>
  <th>综合分</th>
</tr>
{''.join(rows)}
</table>
</body></html>"""

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    logger.info(f"💾 HTML 报告已保存: {path}")
    return path


# save_html 别名（向后兼容）
save_html = save_html_report


# ═══════════════════════════════════════════════════════
# 控制台输出
# ═══════════════════════════════════════════════════════

def print_results_table(merged: list[dict]) -> None:
    """在控制台输出富文本表格。"""
    table = Table(title="📊 综合排名", header_style="bold magenta")
    for col in ("排名", "代码", "TA信号", "置信度", "Kronos方向", "预期涨幅%", "Kronos置信", "综合分", "降级模式"):
        table.add_column(col, justify="right" if col != "代码" else "left")

    for m in merged[:20]:
        pu = m.get("kronos_prediction_uncertainty") or {}
        cs = pu.get("confidence_score")
        cs_str = f"{cs:.1f}" if cs is not None else "-"
        dmg = m.get("degradation_mode")
        _, dmg_str = _degradation_badge(dmg)
        table.add_row(
            str(m.get("rank", "-")),
            m.get("ticker", "-"),
            str(m.get("ta_signal") or "-"),
            str(m.get("ta_confidence") or "-"),
            str(m.get("kronos_direction") or "-"),
            f"{m.get('kronos_change_pct') or 0:.2f}",
            cs_str,
            f"[bold]{m.get('composite_score') or '-'}[/bold]",
            dmg_str,
        )
    console.print(table)


# print_table 别名（向后兼容）
print_table = print_results_table


def print_results_summary(merged: list[dict], date: str) -> None:
    """打印简要摘要。"""
    console.print(f"\n[bold cyan]📅 分析日期: {date}[/bold cyan]")
    console.print(f"[bold cyan]📈 共分析 {len(merged)} 只股票[/bold cyan]\n")

    # 统计降级情况
    n_degraded = sum(1 for m in merged if m.get("degradation_mode"))
    n_kronos_degraded = sum(
        1 for m in merged if m.get("degradation_mode") == "kronos_degraded"
    )
    n_cache_fallback = sum(
        1 for m in merged if m.get("degradation_mode") == "ta_cache_fallback"
    )
    if n_degraded:
        parts = []
        if n_kronos_degraded:
            parts.append(f"[yellow]⚠️  {n_kronos_degraded} 只 Kronos 不可用（TA-only）[/yellow]")
        if n_cache_fallback:
            parts.append(f"[cyan]📦 {n_cache_fallback} 只 TA 使用缓存回退[/cyan]")
        console.print("  ".join(parts))

    if merged:
        best = merged[0]
        pu = best.get("kronos_prediction_uncertainty") or {}
        cs = pu.get("confidence_score")
        cs_str = f" (Kronos置信={cs:.1f})" if cs is not None else ""
        dmg = best.get("degradation_mode")
        _, dmg_rich = _degradation_badge(dmg)
        dmg_note = f" {dmg_rich}" if dmg_rich != "—" else ""
        console.print(
            f"[bold green]🥇 最佳推荐: {best['ticker']} "
            f"(TA={best.get('ta_signal')}  Kronos={best.get('kronos_direction')} "
            f"综合分={best.get('composite_score')}{cs_str}){dmg_note}[/bold green]"
        )

    buys = [m for m in merged if m.get("ta_signal") == "BUY"]
    if buys:
        console.print(f"[green]💚 BUY 信号: {len(buys)} 只[/green]")
        for m in buys[:5]:
            pu = m.get("kronos_prediction_uncertainty") or {}
            cs = pu.get("confidence_score")
            cs_str = f" Kronos置信={cs:.1f}" if cs is not None else ""
            console.print(
                f"   • {m['ticker']}: 置信度={m.get('ta_confidence')} "
                f"Kronos预期={m.get('kronos_change_pct') or 0:.2f}%{cs_str}"
            )

    console.print()


# print_summary 别名（向后兼容）
print_summary = print_results_summary
