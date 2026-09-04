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
import sys
from pathlib import Path

# 共享函数（避免与 feishu_notify.py 重复）
from scripts.feishu_utils import (  # noqa: F401
    build_buffett_card,
    build_ci_card,
    build_daily_card,
    send_feishu,
)

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
