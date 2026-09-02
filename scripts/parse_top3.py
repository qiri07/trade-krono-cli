#!/usr/bin/env python3
"""从 outputs/results.json 提取 Top 3 推荐摘要，供 GitHub Actions 使用。

用法：
    uv run python scripts/parse_top3.py
"""

from __future__ import annotations

import json
from pathlib import Path

from trade_krono_cli.utils import strip_ticker_prefix


def parse_top3(results_path: Path = Path("outputs/results.json")) -> str:
    """从 results.json 解析 Top 3 推荐，格式为 'ticker:信号 分数'。"""
    if not results_path.exists():
        return ""
    try:
        data = json.loads(results_path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    # 兼容新旧格式：新项目以 dict {project, results} 形式输出
    if isinstance(data, dict):
        items = data.get("results", [])
    elif isinstance(data, list):
        items = data
    else:
        return ""
    if not items:
        return ""
    parts = []
    for item in items[:3]:
        t = strip_ticker_prefix(item.get("ticker", "?"))
        s = item.get("ta_signal", "?")
        c = item.get("ranking_score") or item.get("composite_score", "?")
        parts.append(f"{t}:{s} {c}")
    return "  /  ".join(parts)


if __name__ == "__main__":
    print(parse_top3())
