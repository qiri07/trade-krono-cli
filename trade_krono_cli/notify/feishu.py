"""
飞书通知模块 — 向飞书群推送消息。

支持两种方式：
1. WEBHOOK_URL（Incoming Webhook，无需签名）
2. APP_ID + APP_SECRET（自定义机器人，需签名验证）

环境变量：
    FEISHU_WEBHOOK_URL  — Incoming Webhook 地址（推荐，最简单）
    FEISHU_APP_ID       — 应用 App ID（需配合 APP_SECRET 使用签名模式）
    FEISHU_APP_SECRET   — 应用 App Secret
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time

import requests
from loguru import logger

# 签名模式下的固定 key
_SIGN_KEY = "XwVam_bKZ"


def _send_webhook(url: str, content: str, is_markdown: bool = True) -> dict:
    """发送飞书 Incoming Webhook 消息。

    Parameters
    ----------
    url : str
        Webhook 地址
    content : str
        消息内容
    is_markdown : bool
        是否使用 markdown 格式

    Returns
    -------
    dict
        API 响应
    """
    payload: dict = {
        "msg_type": "post",
        "content": {
            "post": {
                "zh_cn": {
                    "title": "投研报告",
                    "content": [[{"tag": "text", "text": content}]],
                }
            }
        },
    }
    resp = requests.post(url, json=payload, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _gen_sign(timestamp: str, secret: str) -> str:
    """生成飞书机器人签名。"""
    string_to_sign = timestamp + "\n" + secret
    hmac_code = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
    return base64.b64encode(hmac_code).decode("utf-8")


def _send_signed_webhook(url: str, content: str, app_id: str, app_secret: str) -> dict:
    """发送带签名的飞书 Webhook 消息。

    Parameters
    ----------
    url : str
        Webhook 地址
    content : str
        消息内容
    app_id : str
        应用 App ID
    app_secret : str
        应用 App Secret

    Returns
    -------
    dict
        API 响应
    """
    timestamp = str(int(time.time()))
    sign = _gen_sign(timestamp, app_secret)

    payload: dict = {
        "timestamp": timestamp,
        "sign": sign,
        "msg_type": "post",
        "content": {
            "post": {
                "zh_cn": {
                    "title": "投研报告",
                    "content": [[{"tag": "text", "text": content}]],
                }
            }
        },
    }
    resp = requests.post(url, json=payload, timeout=15)
    resp.raise_for_status()
    return resp.json()


def send_feishu(
    content: str,
    webhook_url: str | None = None,
    app_id: str | None = None,
    app_secret: str | None = None,
) -> bool:
    """发送飞书消息通知。

    Parameters
    ----------
    content : str
        消息正文（支持多行，每行一条记录）
    webhook_url : str | None
        Incoming Webhook 地址；默认读取 FEISHU_WEBHOOK_URL
    app_id : str | None
        应用 App ID；默认读取 FEISHU_APP_ID
    app_secret : str | None
        应用 App Secret；默认读取 FEISHU_APP_SECRET

    Returns
    -------
    bool
        是否发送成功
    """
    url = webhook_url or os.getenv("FEISHU_WEBHOOK_URL", "").strip()
    app_id_val = app_id or os.getenv("FEISHU_APP_ID", "").strip()
    app_secret_val = app_secret or os.getenv("FEISHU_APP_SECRET", "").strip()

    if not url:
        logger.warning("⚠️  未配置 FEISHU_WEBHOOK_URL，跳过飞书推送")
        return False

    try:
        if app_id_val and app_secret_val:
            result = _send_signed_webhook(url, content, app_id_val, app_secret_val)
        else:
            result = _send_webhook(url, content)

        if result.get("code") == 0 or result.get("StatusCode") == 0:
            logger.info("✅ 飞书消息发送成功")
            return True
        else:
            logger.error(f"❌ 飞书发送失败: {result}")
            return False
    except Exception as e:
        logger.error(f"❌ 飞书推送异常: {e}")
        return False
