"""飞书 Webhook 通知 — 将 GitHub Actions 运行结果推送到飞书群。

支持多种模式：
  1. CI 模式：报告 lint / type-check / test 矩阵结果
  2. Daily 模式：报告投研分析结果摘要
  3. Buffett 模式：报告巴菲特六闸门筛选结果

调用方式（两种）：
  # 方式 1：直接使用（原有方式，兼容）
  python scripts/feishu_notify.py buffett --result-file outputs/results/buffett_screen.txt --url "https://..."

  # 方式 2：通过 CLI 工具（推荐，自动读取本地配置）
  python scripts/feishu_cli.py buffett --result-file outputs/results/buffett_screen.txt

使用方式：
  # CI 结果
  python scripts/feishu_notify.py ci \
    --status success \
    --branch master \
    --commit abc123 \
    --jobs 'lint✅ type-check✅ test✅' \
    --url "https://open.feishu.cn/open-apis/bot/v2/hook/xxx"

  # Daily 结果
  python scripts/feishu_notify.py daily \
    --status success \
    --date 2026-08-27 \
    --tickers "600519,000858" \
    --run-url "https://github.com/xxx/runs/123" \
    --url "https://open.feishu.cn/open-apis/bot/v2/hook/xxx"

  # 巴菲特筛选结果
  python scripts/feishu_notify.py buffett \
    --result-file outputs/results/buffett_screen_20260903.txt \
    --url "https://open.feishu.cn/open-apis/bot/v2/hook/xxx"
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 共享函数（避免重复）
from scripts.feishu_utils import (
    build_buffett_card,
    build_ci_card,
    build_daily_card,
    send_feishu,
)
from trade_krono_cli.utils import strip_ticker_prefix


def _read_top3_from_results() -> str:
    """从 outputs/results.json 读取 Top 3 推荐摘要。"""
    results_path = Path("outputs/results.json")
    if not results_path.exists():
        return "（无报告生成）"
    try:
        data = json.loads(results_path.read_text(encoding="utf-8"))
    except Exception:
        return "（解析结果文件失败）"
    # 兼容新旧格式：新项目以 dict {project, results} 形式输出
    if isinstance(data, dict):
        items = data.get("results", [])
    elif isinstance(data, list):
        items = data
    else:
        return "（未知结果格式）"
    if not items:
        return "（无推荐结果）"
    parts = []
    for item in items[:3]:
        t = strip_ticker_prefix(item.get("ticker", "?"))
        s = item.get("ta_signal", "?")
        c = item.get("ranking_score") or item.get("composite_score", "?")
        parts.append(f"{t}:{s} {c}")
    return "  /  ".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser(description="飞书 Webhook 通知")
    sub = parser.add_subparsers(dest="mode", required=True)

    # ci 子命令
    p_ci = sub.add_parser("ci", help="CI 流水线结果通知")
    p_ci.add_argument("--status", required=True, choices=["success", "failure", "cancelled"])
    p_ci.add_argument("--branch", required=True, help="分支名")
    p_ci.add_argument("--commit", required=True, help="Commit SHA（短）")
    p_ci.add_argument(
        "--jobs",
        required=True,
        help="各 Job 结果摘要，如 'lint✅ type-check✅ test✅'",
    )
    p_ci.add_argument("--run-url", required=True, help="GitHub Runs URL")
    p_ci.add_argument("--url", required=True, help="飞书 Webhook URL")

    # daily 子命令
    p_daily = sub.add_parser("daily", help="每日投研分析结果通知")
    p_daily.add_argument("--status", required=True, choices=["success", "failure", "cancelled"])
    p_daily.add_argument("--date", required=True, help="分析日期 YYYY-MM-DD")
    p_daily.add_argument("--tickers", default="", help="股票代码列表")
    p_daily.add_argument("--top3", default="", help="Top 3 推荐摘要")
    p_daily.add_argument("--content", default="", help="分析摘要内容（可选，支持多行）")
    p_daily.add_argument("--run-url", required=True, help="GitHub Runs URL")
    p_daily.add_argument("--url", required=True, help="飞书 Webhook URL")

    # buffett 子命令
    p_buffett = sub.add_parser("buffett", help="巴菲特六闸门筛选结果通知")
    p_buffett.add_argument("--result-file", required=True, help="筛选结果文件路径")
    p_buffett.add_argument("--url", required=True, help="飞书 Webhook URL")

    args = parser.parse_args()

    if args.mode == "ci":
        payload = build_ci_card(args.status, args.branch, args.commit, args.jobs, args.run_url)
    elif args.mode == "daily":
        top3 = args.top3 or _read_top3_from_results()
        payload = build_daily_card(
            args.status,
            args.date,
            args.tickers,
            top3,
            args.run_url,
            content=args.content,
        )
    elif args.mode == "buffett":
        payload = build_buffett_card(args.result_file)
    else:
        payload = {"msg_type": "text", "text": "未知模式"}

    ok = send_feishu(args.url, payload)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
