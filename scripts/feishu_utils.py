"""scripts/feishu_utils.py — 飞书推送唯一库实现层。

提供配置管理、卡片构建、WebSocket 发送、统一发送分发；
所有上层调用（feishu_cli、各选股/分析脚本）均应仅依赖本模块。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import ssl
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from urllib import parse as _urllib_parse
from urllib import request as _urllib_request

from loguru import logger

_CST = timezone(timedelta(hours=8))
_FEISHU_CLI = Path(__file__).resolve().parent / "feishu_cli.py"
_DEFAULT_CONFIG = Path("~/.config/feishu-notify/config.json").expanduser()


def push_feishu(mode: str, **kwargs: object) -> bool:
    """统一调用 feishu_cli.py 子进程推送飞书消息。

    所有脚本的飞书推送均应通过此函数，保证通知格式与调用方式一致。

    Parameters
    ----------
    mode : str
        feishu_cli 子命令：ci / daily / buffett / text
    **kwargs
        对应子命令的参数（键名用下划线，自动转为连字符，见 feishu_cli.py）

    Returns
    -------
    bool
        是否推送成功
    """

    try:
        args: list[str] = [sys.executable, str(_FEISHU_CLI), mode]
        for key, value in kwargs.items():
            if value is not None and str(value).strip():
                args += [f"--{key.replace('_', '-')}", str(value)]
        result = subprocess.run(args, capture_output=True, text=True, timeout=20)
        if result.returncode == 0:
            logger.info(f"✅ 飞书推送成功 [{mode}]")
            return True
        logger.warning(f"⚠️ 飞书推送失败 [{mode}]：{result.stderr.strip()}")
        return False
    except subprocess.TimeoutExpired:
        logger.error(f"❌ 飞书推送超时 [{mode}]")
        return False
    except Exception as e:
        logger.opt(exception=True).error(f"❌ 飞书推送异常 [{mode}]：{e}")
        return False


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


def build_buffett_card(result_file: str, ai_summary: str = "") -> dict:
    """从巴菲特筛选结果文件构建飞书卡片，可选择追加 AI 核实摘要。

    Parameters
    ----------
    result_file : str
        筛选结果文件路径
    ai_summary : str, optional
        AI 核实摘要文本（Markdown 格式），追加到卡片末尾

    Returns
    -------
    dict
        飞书卡片结构
    """
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

    # 构建股票列表文本（仅显示名称，每行三个）
    max_show = min(len(stocks), 80)  # 最多显示80只，避免消息过长
    names = [s["name"] for s in stocks[:max_show]]
    chunks = [names[i : i + 3] for i in range(0, len(names), 3)]
    stock_lines = ["、".join(f"**{n}**" for n in c) for c in chunks]
    if len(stocks) > max_show:
        stock_lines.append(f"... 共 {len(stocks)} 只")
    elif len(stocks) == 0:
        stock_lines.append("（无通过股票）")

    stock_text = "\n".join(stock_lines) if stock_lines else "（无通过股票）"

    # 提取失败统计（按闸门类别汇总，不展开单值明细）
    fail_summary: list[str] = []  # 直接保留的摘要行
    fail_gate_counts: dict[str, int] = {}  # 按闸门汇总计数

    in_fail = False
    for line in lines:
        if "失败分布" in line:
            in_fail = True
            continue
        if not in_fail:
            continue
        if not line.startswith("  "):
            break
        stripped = line.strip()
        # 单值明细行：格式为 "①PE=xxx PB=xxx: 1 只" 或含 None 的单值行
        is_detail = bool(re.match(r"^[①②③④⑤]", stripped)) and ": 1 只" in stripped
        if is_detail:
            gate = stripped[0]
            fail_gate_counts[gate] = fail_gate_counts.get(gate, 0) + 1
        else:
            fail_summary.append(stripped)

    # 将汇总的闸门计数追加到失败统计末尾
    gate_labels = {
        "①": "①PE/PB不达标",
        "②": "②ROE不达标",
        "③": "③负债率不达标",
        "④": "④现金流不达标",
        "⑤": "⑤CAGR不达标",
    }
    for gate, label in gate_labels.items():
        if gate in fail_gate_counts:
            fail_summary.append(f"{label}: {fail_gate_counts[gate]} 只")

    fail_text = "\n".join(fail_summary) if fail_summary else "（无失败统计）"

    # 解析日期
    date_match = re.search(r"(\d{4}-\d{2}-\d{2})", header)
    date_str = date_match.group(1) if date_match else _now_cn()[:10]

    # 构建主内容
    main_content = (
        f"**筛选日期：** {date_str}  "
        f"**通过数量：** {pass_count} 只\n\n"
        f"**通过股票：**\n{stock_text}\n\n"
        f"**失败分布：**\n{fail_text}"
    )

    # 追加 AI 核实摘要
    if ai_summary and ai_summary.strip():
        model_name = os.getenv("AI_VERIFICATION_MODEL", "nemotron-3.5-super-vl-120b-a12b")
        main_content += f"\n\n{'─' * 30}\n**🤖 AI 核实分析（{model_name}）**\n{ai_summary}"

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
                        "content": main_content,
                    },
                },
            ],
        },
    }


def send_feishu(url: str, payload: dict, secret: str | None = None) -> bool:
    """发送飞书 Webhook 请求，返回是否成功。

    Parameters
    ----------
    url : str
        飞书机器人 Webhook URL
    payload : dict
        消息卡片字典
    secret : str | None, optional
        加签密钥。若提供，则自动计算 sign 参数并追加到 URL 中。

    Returns
    -------
    bool
        是否发送成功
    """
    # 加签：timestamp + sign 追加为 URL query 参数
    if secret:
        ts = int(time.time())
        string_to_sign = f"{ts}\n{secret}"
        sign = base64.b64encode(
            hmac.new(
                secret.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha256
            ).digest()
        ).decode("utf-8")
        parsed = _urllib_parse.urlparse(url)
        query = _urllib_parse.parse_qs(parsed.query, keep_blank_values=True)
        query["timestamp"] = [str(ts)]
        query["sign"] = [sign]
        url = _urllib_parse.urlunparse(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                parsed.params,
                _urllib_parse.urlencode(query, doseq=True),
                parsed.fragment,
            )
        )

    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = _urllib_request.Request(  # noqa: S310
        url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    ctx = ssl.create_default_context()
    try:
        with _urllib_request.urlopen(req, timeout=10, context=ctx) as resp:  # noqa: S310
            body = resp.read().decode("utf-8")
            result = json.loads(body)
            ok = result.get("code") == 0 or result.get("StatusCode") == 0
            if not ok:
                logger.warning(f"飞书返回错误: {result}")
            else:
                logger.info("飞书推送成功")
            return ok
    except Exception as e:
        logger.opt(exception=True).error(f"飞书推送失败: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# 配置管理（原 feishu_core.py 独有，下沉至此，使 feishu_utils 成为唯一库层）
# ─────────────────────────────────────────────────────────────────────────────


def load_config(
    config_path: str | Path = _DEFAULT_CONFIG,
) -> dict:
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
    ValueError
        配置文件中既不包含 webhook_url 也不包含 channels
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

    # fallback 到根 webhook_url
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
# 统一发送接口（原 feishu_core.py send_notification，下沉至此）
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
        logger.error(f"❌ 未知飞书通知模式: {mode}")
        return False

    secret = config.get("webhook_secret") or config.get("channels", {}).get(channel, {}).get(
        "webhook_secret"
    )
    return send_feishu(url, payload, secret=secret)
