"""scripts/feishu_utils.py — 飞书通知共享工具函数。

从 feishu_core.py 和 feishu_notify.py 中提取的公共实现，
避免重复代码，降低圈复杂度。
"""

from __future__ import annotations

import json
import re
import ssl
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from urllib import request as _urllib_request

_CST = timezone(timedelta(hours=8))


def _now_cn() -> str:
    """返回北京时间字符串。"""
    return datetime.now(_CST).strftime("%Y-%m-%d %H:%M:%S")


def _template(status: Literal["success", "failure", "cancelled"]) -> str:
    """返回状态对应的颜色模板。"""
    return {"success": "green", "failure": "red", "cancelled": "grey"}.get(status, "blue")


def _status_emoji(status: Literal["success", "failure", "cancelled"]) -> str:
    """返回状态对应的 emoji。"""
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
                    "content": f"🔧 trade-krono-cli CI 流水线 · {_status_emoji(status)}",
                },
                "template": _template(status),
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": (
                            f"**项目：** trade-krono-cli &nbsp; **分支：** `{branch}`  "
                            f"**Commit：** `{commit[:8]}`\n"
                            f"**时间：** {_now_cn()}\n"
                            f"**结果：** {jobs}\n"
                            f"[📎 查看 Runs →]({run_url})"
                        ),
                    },
                },
            ],
        },
    }


def build_daily_card(
    status: Literal["success", "failure", "cancelled"],
    date: str,
    tickers: str,
    top3: str,
    run_url: str,
    content: str = "",
) -> dict:
    """构建每日分析结果飞书卡片。"""
    sections = [f"**日期：** {date}  **股票：** {tickers or '全市场自动筛选'}"]
    if top3:
        sections.append(f"**Top 3 推荐：**\n{top3}")
    if content:
        sections.append(f"**📋 分析摘要：**\n{content}")
    return {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": f"📊 trade-krono-cli 每日投研分析 · {_status_emoji(status)}",
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
                        },
                    ],
                },
            ],
        },
    }


def build_buffett_card(result_file: str) -> dict:
    """从巴菲特筛选结果文件构建飞书卡片。"""
    path = Path(result_file)
    if not path.exists():
        return {
            "msg_type": "text",
            "text": f"❌ 结果文件不存在: {result_file}",
        }

    content = path.read_text(encoding="utf-8")
    lines = content.split("\n")

    # 解析头部信息
    header = lines[0] if lines else ""
    summary_line = lines[1] if len(lines) > 1 else ""

    # 提取通过数量
    m = re.search(r"通过五闸门.*?共\s*(\d+)\s*只", summary_line)
    pass_count = int(m.group(1)) if m else 0

    # 解析股票列表（跳过表头行）
    stocks: list[dict] = []
    in_table = False
    for line in lines[3:]:
        stripped = line.strip()
        if not stripped:
            continue
        if "--------" in line and not in_table:
            in_table = True
            continue
        if in_table and stripped and not stripped.startswith("失败分布"):
            parts = stripped.split()
            if len(parts) >= 2 and any(c.isdigit() for c in parts[0]):
                stocks.append(
                    {
                        "code": parts[0],
                        "name": parts[1],
                        "pe": parts[2] if len(parts) > 2 else "?",
                        "pb": parts[3] if len(parts) > 3 else "?",
                        "roe": parts[4] if len(parts) > 4 else "?",
                    }
                )
        elif in_table:
            break

    # 构建股票列表文本
    stock_lines = []
    for s in stocks[:15]:
        stock_lines.append(
            f"• **{s['code']} {s['name']}** &nbsp; PE={s['pe']} &nbsp; ROE={s['roe']}%"
        )
    if len(stocks) > 15:
        stock_lines.append(f"... 共 {len(stocks)} 只")

    stock_text = "\n".join(stock_lines) if stock_lines else "（无通过股票）"

    # 提取失败统计
    fail_lines = []
    in_fail = False
    for line in lines:
        if "失败分布" in line:
            in_fail = True
            continue
        if in_fail and line.strip().startswith("  "):
            fail_lines.append(line.strip())
        elif in_fail:
            break

    fail_text = "\n".join(fail_lines[:5]) if fail_lines else "（无失败统计）"
    if len(fail_lines) > 5:
        fail_text += f"\n... 共 {len(fail_lines)} 种失败原因"

    # 解析日期
    date_match = re.search(r"(\d{4}-\d{2}-\d{2})", header)
    date_str = date_match.group(1) if date_match else _now_cn()[:10]

    return {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": f"📈 巴菲特六闸门筛选结果 · {_status_emoji('success' if pass_count > 0 else 'failure')}",
                },
                "template": "green" if pass_count > 0 else "red",
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": (
                            f"**筛选日期：** {date_str}  "
                            f"**通过数量：** {pass_count} 只\n\n"
                            f"**通过股票：**\n{stock_text}\n\n"
                            f"**失败分布：**\n{fail_text}"
                        ),
                    },
                },
            ],
        },
    }


def send_feishu(url: str, payload: dict) -> bool:
    """发送飞书 Webhook 请求，返回是否成功。"""
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = _urllib_request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    ctx = ssl.create_default_context()
    try:
        with _urllib_request.urlopen(req, timeout=10, context=ctx) as resp:
            body = resp.read().decode("utf-8")
            result = json.loads(body)
            ok = result.get("code") == 0 or result.get("StatusCode") == 0
            if not ok:
                print(f"⚠️ 飞书返回错误: {result}", file=sys.stderr)
            else:
                print("✅ 飞书推送成功")
            return ok
    except Exception as e:
        print(f"❌ 飞书推送失败: {e}", file=sys.stderr)
        return False
