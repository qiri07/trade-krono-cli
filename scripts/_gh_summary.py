"""GitHub Actions 显示运行结果摘要 — 仅用于 daily-run.yml 工作流。"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> None:
    results_path = Path("outputs/results.json")
    if not results_path.exists():
        print("⚠️ 未找到 results.json", file=sys.stderr)
        return

    try:
        data = json.loads(results_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"解析结果文件失败: {e}", file=sys.stderr)
        return

    # 兼容新旧格式：新项目以 dict 形式输出，含 project / results 键
    if isinstance(data, dict):
        project = data.get("project", "")
        items = data.get("results", [])
        print(f"📊 {project} · 共 {data.get('count', len(items))} 只")
    elif isinstance(data, list):
        project = "trade-krono-cli"
        items = data
    else:
        print(f"未知的结果格式: {type(data).__name__}", file=sys.stderr)
        return

    for item in items[:5]:
        ticker = item.get("ticker", "?")
        signal = item.get("ta_signal", "?")
        score = item.get("ranking_score") or item.get("composite_score", "?")
        print(f"  {ticker}  {signal}  score={score}")


if __name__ == "__main__":
    main()
