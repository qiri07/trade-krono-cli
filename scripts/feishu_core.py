"""飞书推送核心模块 — 支持多种通知模式。

本模块提供：
1. 卡片构建函数（CI/Daily/Buffett/Text）
2. Webhook 发送函数
3. 配置管理（从 JSON 文件读取 Webhook URL）

使用方式：
    from feishu_core import send_notification, load_config

    # 加载配置
    config = load_config("~/.config/feishu-notify/config.json")

    # 发送通知
    ok = send_notification(
        mode="buffett",
        result_file="outputs/results/buffett_screen.txt",
        config=config
    )
"""

from __future__ import annotations

import json
import re
import ssl
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

# 飞书北京时间
_CST = timezone(timedelta(hours=8))


# ─────────────────────────────────────────────────────────────────────────────
# 配置管理
# ─────────────────────────────────────────────────────────────────────────────


def load_config(config_path: str | Path = "~/.config/feishu-notify/config.json") -> dict:
    """加载飞书推送配置。

    Parameters
    ----------
    config_path : str | Path
        配置文件路径，默认 ~/.config/feishu-notify/config.json

    Returns
    -------
    dict
        配置字典，包含 webhook_url、app_id、app_secret、channels 等

    Raises
    ------
    FileNotFoundError
        配置文件不存在
    json.JSONDecodeError
        配置文件 JSON 格式错误
    """
    path = Path(config_path).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"配置文件不存在: {path}")

    data = json.loads(path.read_text(encoding="utf-8"))

    # 验证必需字段
    if "webhook_url" not in data and not data.get("channels"):
        raise ValueError("配置文件中必须包含 webhook_url 或 channels")

    return data


def get_webhook_url(config: dict, channel: str = "default") -> str:
    """从配置中获取指定频道的 Webhook URL。

    Parameters
    ----------
    config : dict
        配置字典
    channel : str
        频道名称，默认为 "default"

    Returns
    -------
    str
        Webhook URL

    Raises
    ------
    ValueError
        未找到对应频道的 Webhook URL
    """
    # 优先从 channels 中查找
    channels = config.get("channels", {})
    if channel in channels:
        return channels[channel]["webhook_url"]

    #  fallback 到根 webhook_url
    url = config.get("webhook_url", "").strip()
    if not url:
        raise ValueError(f"未找到频道 '{channel}' 的 Webhook URL")

    return url


# ─────────────────────────────────────────────────────────────────────────────
# 卡片构建函数
# ─────────────────────────────────────────────────────────────────────────────


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
                            f"**项目：** trade-krono-cli &nbsp; **分支：** `{branch}`  **Commit：** `{commit[:8]}`\n"
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
    for line in lines[3:]:  # 跳过前两行标题
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
    for s in stocks[:15]:  # 最多显示15只
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
                    "content": f"📊 巴菲特六闸门筛选结果 · {'✅' if pass_count > 0 else '⚪'}",
                },
                "template": "green" if pass_count > 0 else "grey",
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": (
                            f"**日期：** {date_str}  \n"
                            f"**筛选范围：** 全市场 A 股（过滤 ST/次新）  \n"
                            f"**通过五闸门（①~⑤）：** **{pass_count} 只**\n"
                        ),
                    },
                },
                {"tag": "hr"},
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"**🏆 通过股票列表：**\n\n{stock_text}",
                    },
                },
                {"tag": "hr"},
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"**📉 失败分布（前5）：**\n\n{fail_text}",
                    },
                },
                {
                    "tag": "note",
                    "elements": [
                        {
                            "tag": "plain_text",
                            "content": "闸门规则：①PE<16且PB<3 ②ROE>15%且扣非ROE>12% ③负债率<50% ④CFO>0 ⑤3年净利CAGR>0",
                        }
                    ],
                },
            ],
        },
    }


def build_text_card(content: str, title: str = "通知") -> dict:
    """构建通用文本飞书卡片。

    Parameters
    ----------
    content : str
        消息内容（支持多行）
    title : str
        卡片标题，默认为 "通知"

    Returns
    -------
    dict
        飞书卡片结构
    """
    return {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": f"📢 {title}",
                },
                "template": "blue",
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": content,
                    },
                },
            ],
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# Webhook 发送
# ─────────────────────────────────────────────────────────────────────────────


def send_feishu(url: str, payload: dict) -> bool:
    """发送飞书 Webhook 请求，返回是否成功。

    Parameters
    ----------
    url : str
        Webhook 地址
    payload : dict
        飞书消息体

    Returns
    -------
    bool
        是否发送成功
    """
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
            # 飞书 Webhook 成功码：code==0 或 StatusCode==0（兼容不同版本）
            ok = result.get("code") == 0 or result.get("StatusCode") == 0
            if not ok:
                print(f"⚠️ 飞书返回错误: {result}", file=sys.stderr)
            else:
                print("✅ 飞书推送成功")
            return ok
    except Exception as e:
        print(f"❌ 飞书推送失败: {e}", file=sys.stderr)
        return False


# ─────────────────────────────────────────────────────────────────────────────
# 统一发送接口
# ─────────────────────────────────────────────────────────────────────────────


def send_notification(
    mode: str,
    config: dict,
    **kwargs: object,
) -> bool:
    """统一通知发送接口。

    Parameters
    ----------
    mode : str
        通知模式：ci / daily / buffett / text
    config : dict
        配置字典（来自 load_config）
    **kwargs : object
        各模式所需参数：
        - ci: status, branch, commit, jobs, run_url
        - daily: status, date, tickers, top3, run_url, content
        - buffett: result_file
        - text: content, title

    Returns
    -------
    bool
        是否发送成功
    """
    # 获取 Webhook URL
    channel = str(kwargs.get("channel", "default"))
    url = get_webhook_url(config, channel)

    # 根据模式构建卡片
    if mode == "ci":
        payload = build_ci_card(
            status=kwargs["status"],  # type: ignore[arg-type]
            branch=str(kwargs["branch"]),
            commit=str(kwargs["commit"]),
            jobs=str(kwargs["jobs"]),
            run_url=str(kwargs["run_url"]),
        )
    elif mode == "daily":
        payload = build_daily_card(
            status=kwargs["status"],  # type: ignore[arg-type]
            date=str(kwargs["date"]),
            tickers=str(kwargs.get("tickers", "")),
            top3=str(kwargs.get("top3", "")),
            run_url=str(kwargs["run_url"]),
            content=str(kwargs.get("content", "")),
        )
    elif mode == "buffett":
        payload = build_buffett_card(str(kwargs["result_file"]))
    elif mode == "text":
        payload = build_text_card(
            content=str(kwargs["content"]),
            title=str(kwargs.get("title", "通知")),
        )
    else:
        print(f"❌ 未知模式: {mode}", file=sys.stderr)
        return False

    return send_feishu(url, payload)
