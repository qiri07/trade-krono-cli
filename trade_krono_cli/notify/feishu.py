"""飞书通知模块 — 向飞书群推送消息。

本模块封装了飞书推送功能，底层调用独立的 CLI 工具。

环境变量：
    FEISHU_WEBHOOK_URL  — Incoming Webhook 地址（备用）
    FEISHU_APP_ID       — 应用 App ID（需配合 APP_SECRET 使用签名模式，备用）
    FEISHU_APP_SECRET   — 应用 App Secret（备用）

配置优先级：
    1. ~/.config/feishu-notify/config.json（推荐）
    2. FEISHU_WEBHOOK_URL 环境变量（备用）

使用方式：
    from trade_krono_cli.notify import send_feishu

    # 发送简单文本
    send_feishu("投研分析完成")

    # 指定频道
    send_feishu("告警消息", channel="alerts")
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Literal

from loguru import logger

# 飞书推送 CLI 脚本路径（相对于项目根目录）
_FEISHU_CLI_PATH = Path(__file__).resolve().parents[1] / "scripts" / "feishu_cli.py"
_DEFAULT_CONFIG_PATH = Path.home() / ".config" / "feishu-notify" / "config.json"


def send_feishu(
    content: str,
    webhook_url: str | None = None,
    app_id: str | None = None,
    app_secret: str | None = None,
    mode: Literal["text", "ci", "daily", "buffett"] = "text",
    channel: str = "default",
    **kwargs: object,
) -> bool:
    """发送飞书消息通知。

    Parameters
    ----------
    content : str
        消息正文（text 模式必需，其他模式忽略）
    webhook_url : str | None
        Incoming Webhook 地址（已废弃，推荐使用配置文件）
    app_id : str | None
        应用 App ID（已废弃，推荐使用配置文件）
    app_secret : str | None
        应用 App Secret（已废弃，推荐使用配置文件）
    mode : Literal["text", "ci", "daily", "buffett"]
        通知模式
    channel : str
        推送频道
    **kwargs : object
        各模式所需参数：
        - text: content（必需）
        - ci: status, branch, commit, jobs, run_url
        - daily: status, date, tickers, top3, run_url
        - buffett: result_file

    Returns
    -------
    bool
        是否发送成功
    """
    # 检查配置文件是否存在
    config_path = _find_config()

    # 构建命令
    cmd = [sys.executable, str(_FEISHU_CLI_PATH), mode, "--channel", channel]

    if config_path:
        cmd.extend(["--config", str(config_path)])

    # 添加各模式的参数
    if mode == "text":
        cmd.extend(["--content", str(content)])
    elif mode == "ci":
        cmd.extend(
            [
                "--status",
                str(kwargs.get("status", "success")),
                "--branch",
                str(kwargs.get("branch", "")),
                "--commit",
                str(kwargs.get("commit", "")),
                "--jobs",
                str(kwargs.get("jobs", "")),
                "--run-url",
                str(kwargs.get("run_url", "")),
            ]
        )
    elif mode == "daily":
        cmd.extend(
            [
                "--status",
                str(kwargs.get("status", "success")),
                "--date",
                str(kwargs.get("date", "")),
                "--tickers",
                str(kwargs.get("tickers", "")),
                "--top3",
                str(kwargs.get("top3", "")),
                "--run-url",
                str(kwargs.get("run_url", "")),
            ]
        )
    elif mode == "buffett":
        result_file = kwargs.get("result_file", "")
        if not result_file:
            logger.warning("⚠️  buffett 模式缺少 result_file 参数")
            return False
        cmd.extend(["--result-file", str(result_file)])
    else:
        logger.warning(f"⚠️  未知模式: {mode}")
        return False

    # 执行 CLI 命令
    try:
        logger.info(f"📤 发送飞书通知: {mode} channel={channel}")
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            logger.info("✅ 飞书消息发送成功")
            return True
        logger.error(f"❌ 飞书推送失败: {result.stderr.strip()}")
        return False
    except subprocess.TimeoutExpired:
        logger.error("❌ 飞书推送超时（30s）")
        return False
    except FileNotFoundError:
        logger.error(f"❌ 找不到飞书推送 CLI: {_FEISHU_CLI_PATH}")
        return False
    except Exception as e:
        logger.error(f"❌ 飞书推送异常: {e}")
        return False


def _find_config() -> Path | None:
    """查找飞书推送配置文件。

    Returns
    -------
    Path | None
        配置文件路径，不存在则返回 None
    """
    # 优先检查环境变量 FEISHU_CONFIG_PATH
    env_config = os.getenv("FEISHU_CONFIG_PATH", "").strip()
    if env_config:
        config_path = Path(env_config).expanduser()
        if config_path.exists():
            return config_path

    # 检查项目默认配置
    if _DEFAULT_CONFIG_PATH.exists():
        return _DEFAULT_CONFIG_PATH

    return None
