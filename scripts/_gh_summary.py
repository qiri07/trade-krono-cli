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

    if isinstance(data, list):
        for item in data[:5]:
            ticker = item.get("ticker", "?")
            signal = item.get("ta_signal", "?")
            score = item.get("composite_score", "?")
            print(f"  {ticker}  {signal}  score={score}")
    elif isinstance(data, dict):
        print(json.dumps(data, indent=2, ensure_ascii=False)[:1000])
    else:
        print(f"未知的结果格式: {type(data).__name__}", file=sys.stderr)


if __name__ == "__main__":
    main()
