"""six_dim 选股 — 报告写入。

将六维选股结果写出 JSON + Markdown 报告。
"""

from __future__ import annotations

import json
from pathlib import Path

from scripts.six_dim.constants import DIM_PRIORITY, DIM_WEIGHTS, TIER_WIDTH
from scripts.six_dim.models import StockScore


def write_report(
    results: list[StockScore],
    date_str: str,
    out_dir: Path,
    funnel: dict[str, int] | None = None,
    total_passed: int | None = None,
) -> tuple[Path, Path]:
    """把结果写出 JSON + Markdown 报告。

    Args:
        results: 排序后的打分结果（可能已被 ``--top`` 截断）。
        date_str: 日期字符串（YYYY-MM-DD）。
        out_dir: 输出目录（``outputs/results``）。
        funnel: 各关卡人数统计，写入 JSON 便于事后审计排序口径与淘汰分布。
        total_passed: 通过全部门槛的股票总数（截断前），缺省记为 ``len(results)``。

    Returns:
        ``(json 路径, markdown 路径)``。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    dc = date_str.replace("-", "")
    payload = {
        "date": date_str,
        "count": len(results),
        "weights": dict(DIM_WEIGHTS),
        "rank_basis": "tiered_lexicographic",
        "tier_width": TIER_WIDTH,
        "dim_priority": list(DIM_PRIORITY),
        "gates": {
            "min_roe_avg": None,  # 由调用方填充
            "min_roe_excl_avg": None,
            "max_pb": None,
            "min_market_cap_yi": None,
        },
        "funnel": funnel or {},
        "total_passed": total_passed if total_passed is not None else len(results),
        "results": [r.to_payload() for r in results],
    }
    json_path = out_dir / f"six_dim_screen_{dc}.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    md_lines = [f"# 六维选股结果 — {date_str}", ""]
    md_lines.append("| 排名 | 代码 | 名称 | 逻辑 | 估值 | 位置 | 趋势 | 股东 | 量价 | 综合 |")
    md_lines.append("|------|------|------|------|------|------|------|------|------|")
    for i, r in enumerate(results, 1):
        md_lines.append(
            f"| {i} | {r.code} | {r.name} | {r.logic:.1f} | {r.valuation:.1f} | "
            f"{r.position:.1f} | {r.trend:.1f} | {r.holder:.1f} | {r.vol_price:.1f} | "
            f"{r.composite:.1f} |"
        )
    md_lines.append("")
    md_lines.append("注：「综合」列为六维加权分，仅在六维同档时作为兜底排序依据，不一定单调递减。")
    md_path = out_dir / f"six_dim_screen_{dc}.md"
    md_path.write_text("\n".join(md_lines), encoding="utf-8")

    return json_path, md_path
