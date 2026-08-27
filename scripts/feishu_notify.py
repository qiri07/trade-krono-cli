"""飞书 Webhook 通知 — 将 GitHub Actions 运行结果推送到飞书群。

支持两种模式：
  1. CI 模式：报告 lint / type-check / test 矩阵结果
  2. Daily 模式：报告投研分析结果摘要

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
"""

from __future__ import annotations

import argparse
import json
import ssl
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

# 飞书北京时间
_CST = timezone(timedelta(hours=8))


def _now_cn() -> str:
    return datetime.now(_CST).strftime("%Y-%m-%d %H:%M:%S")


def _template(status: Literal["success", "failure", "cancelled"]) -> str:
    return {"success": "green", "failure": "red", "cancelled": "grey"}.get(status, "blue")


def _status_emoji(status: Literal["success", "failure", "cancelled"]) -> str:
    return {"success": "✅", "failure": "❌", "cancelled": "⏹️"}.get(status, "⚪")


def build_ci_card(
    status: Literal["success", "failure", "cancelled"],
    branch: str,
    commit: str,
    jobs: str,
    run_url: str,
) -> dict:
    """构建 CI 结果飞书卡片。"""
    return {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": f"🔧 CI 流水线 · {_status_emoji(status)}",
                },
                "template": _template(status),
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": (
                            f"**分支：** `{branch}`  **Commit：** `{commit[:8]}`\n"
                            f"**时间：** {_now_cn()}\n"
                            f"**结果：** {jobs}\n"
                            f"[📎 查看 Runs →]({run_url})"
                        ),
                    },
                }
            ],
        },
    }


def build_daily_card(
    status: Literal["success", "failure", "cancelled"],
    date: str,
    tickers: str,
    top3: str,
    run_url: str,
) -> dict:
    """构建每日分析结果飞书卡片。"""
    sections = [f"**日期：** {date}  **股票：** {tickers or '全市场自动筛选'}"]
    if top3:
        sections.append(f"**Top 3 推荐：**\n{top3}")
    return {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": f"📊 每日投研分析 · {_status_emoji(status)}",
                },
                "template": _template(status),
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": "\n".join(sections)},
                },
                {"tag": "hr"},
                {
                    "tag": "action",
                    "actions": [
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "📎 查看分析报告"},
                            "url": run_url,
                            "type": "default",
                        }
                    ],
                },
            ],
        },
    }


def send_feishu(url: str, payload: dict) -> bool:
    """发送飞书 Webhook 请求，返回是否成功。"""
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            body = resp.read().decode("utf-8")
            result = json.loads(body)
            if result.get("code") == 0 or result.get("StatusCode") == 0:
                print("✅ 飞书推送成功")
                return True
            print(f"⚠️ 飞书返回错误: {result}", file=sys.stderr)
            return False
    except Exception as e:
        print(f"❌ 飞书推送失败: {e}", file=sys.stderr)
        return False


def _read_top3_from_results() -> str:
    """从 outputs/results.json 读取 Top 3 推荐摘要。"""
    results_path = Path("outputs/results.json")
    if not results_path.exists():
        return "（无报告生成）"
    try:
        data = json.loads(results_path.read_text(encoding="utf-8"))
    except Exception:
        return "（解析结果文件失败）"
    if isinstance(data, list):
        parts = []
        for item in data[:3]:
            t = item.get("ticker", "?").replace("sh.", "").replace("sz.", "")
            s = item.get("ta_signal", "?")
            c = item.get("composite_score", "?")
            parts.append(f"{t}:{s} {c}")
        return "  /  ".join(parts)
    return "（未知结果格式）"


def main() -> None:
    parser = argparse.ArgumentParser(description="飞书 Webhook 通知")
    sub = parser.add_subparsers(dest="mode", required=True)

    # ci 子命令
    p_ci = sub.add_parser("ci", help="CI 流水线结果通知")
    p_ci.add_argument("--status", required=True, choices=["success", "failure", "cancelled"])
    p_ci.add_argument("--branch", required=True, help="分支名")
    p_ci.add_argument("--commit", required=True, help="Commit SHA（短）")
    p_ci.add_argument(
        "--jobs", required=True, help="各 Job 结果摘要，如 'lint✅ type-check✅ test✅'"
    )
    p_ci.add_argument("--run-url", required=True, help="GitHub Runs URL")
    p_ci.add_argument("--url", required=True, help="飞书 Webhook URL")

    # daily 子命令
    p_daily = sub.add_parser("daily", help="每日投研分析结果通知")
    p_daily.add_argument("--status", required=True, choices=["success", "failure", "cancelled"])
    p_daily.add_argument("--date", required=True, help="分析日期 YYYY-MM-DD")
    p_daily.add_argument("--tickers", default="", help="股票代码列表")
    p_daily.add_argument("--run-url", required=True, help="GitHub Runs URL")
    p_daily.add_argument("--url", required=True, help="飞书 Webhook URL")

    args = parser.parse_args()

    if args.mode == "ci":
        payload = build_ci_card(args.status, args.branch, args.commit, args.jobs, args.run_url)
    else:
        top3 = _read_top3_from_results()
        payload = build_daily_card(
            args.status, args.date, args.tickers, top3, args.run_url
        )

    ok = send_feishu(args.url, payload)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
