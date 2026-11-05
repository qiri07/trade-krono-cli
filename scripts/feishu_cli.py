#!/usr/bin/env python3
"""飞书推送 CLI 工具 — 支持多种通知模式（统一入口）。

使用方式：
  # CI 结果（配置文件模式，推荐）
  python scripts/feishu_cli.py ci \
    --status success --branch master --commit abc123 \
    --jobs 'lint✅ type-check✅ test✅' \
    --run-url "https://github.com/xxx/runs/123"

  # Daily 结果（配置文件模式，推荐）
  python scripts/feishu_cli.py daily \
    --status success --date 2026-08-27 \
    --tickers "600519,000858" --top3 "600519:BUY 0.85" \
    --run-url "https://github.com/xxx/runs/123"

  # 巴菲特筛选结果
  python scripts/feishu_cli.py buffett \
    --result-file outputs/results/buffett_screen_20260903.txt

  # 通用文本
  python scripts/feishu_cli.py text \
    --content "📊 今日分析完成，Top3: 600519 贵州茅台"

  # 直连 Webhook URL 模式（兼容旧 feishu_notify，无需配置文件）
  python scripts/feishu_cli.py buffett \
    --result-file outputs/results/buffett_screen.txt \
    --url "https://open.feishu.cn/open-apis/bot/v2/hook/xxx" \
    --secret "可选的加签密钥"

  # 指定频道 / 配置文件
  python scripts/feishu_cli.py buffett \
    --result-file outputs/results/buffett_screen.txt \
    --channel alerts \
    --config ~/.config/feishu-notify/config.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 确保仓库根在 sys.path 中（供直接 python scripts/feishu_cli.py 使用）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.feishu_utils import (
    build_buffett_card,
    build_ci_card,
    build_daily_card,
    build_text_card,
    get_webhook_url,
    load_config,
    send_feishu,
    send_notification,
)


def _read_top3_from_results() -> str:
    """从 outputs/results.json 读取 Top 3 推荐摘要（兼容 dict/list 格式）。"""
    results_path = Path("outputs/results.json")
    if not results_path.exists():
        return "（无报告生成）"
    try:
        data = json.loads(results_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return "（解析结果文件失败）"
    if isinstance(data, dict):
        items = data.get("results", [])
    elif isinstance(data, list):
        items = data
    else:
        return "（未知结果格式）"
    if not items:
        return "（无推荐结果）"
    from trade_krono_cli.utils import strip_ticker_prefix

    parts: list[str] = []
    for item in items[:3]:
        t = strip_ticker_prefix(item.get("ticker", "?"))
        s = item.get("ta_signal", "?")
        c = item.get("ranking_score") or item.get("composite_score", "?")
        parts.append(f"{t}:{s} {c}")
    return "  /  ".join(parts)


def _resolve_url_and_secret(
    config: dict | None,
    channel: str,
    url: str | None,
    secret: str | None,
) -> tuple[str, str | None]:
    """解析 Webhook URL 与加签密钥。

    优先级：显式 --url > 配置文件 channels[channel] > 配置文件根 webhook_url。
    密钥优先级：显式 --secret > 配置文件。
    """
    if url:
        return url, secret or None
    if config is not None:
        try:
            url = get_webhook_url(config, channel)
        except ValueError:
            url = ""
        secret = (
            secret
            or config.get("webhook_secret")
            or config.get("channels", {}).get(channel, {}).get("webhook_secret")
        )
        return url, secret or None
    return "", None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="飞书推送工具 — 统一通知入口",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--config",
        default="~/.config/feishu-notify/config.json",
        help="配置文件路径（默认: ~/.config/feishu-notify/config.json）",
    )
    parser.add_argument(
        "--channel",
        default="default",
        help="推送频道（默认: default）",
    )
    parser.add_argument(
        "--url",
        default=None,
        help="直连飞书 Webhook URL（优先于配置文件，兼容无配置文件场景）",
    )
    parser.add_argument(
        "--secret",
        default=None,
        help="加签密钥（与 --url 配合使用；留空则不加签）",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="显示详细日志",
    )

    sub = parser.add_subparsers(dest="mode", required=True)

    # ci 子命令
    p_ci = sub.add_parser("ci", help="CI 流水线结果通知")
    p_ci.add_argument("--status", required=True, choices=["success", "failure", "cancelled"])
    p_ci.add_argument("--branch", required=True, help="分支名")
    p_ci.add_argument("--commit", required=True, help="Commit SHA（短）")
    p_ci.add_argument("--jobs", required=True, help="各 Job 结果摘要")
    p_ci.add_argument("--run-url", required=True, help="GitHub Runs URL")

    # daily 子命令
    p_daily = sub.add_parser("daily", help="每日投研分析结果通知")
    p_daily.add_argument("--status", required=True, choices=["success", "failure", "cancelled"])
    p_daily.add_argument("--date", required=True, help="分析日期 YYYY-MM-DD")
    p_daily.add_argument("--tickers", default="", help="股票代码列表")
    p_daily.add_argument("--top3", default="", help="Top 3 推荐摘要")
    p_daily.add_argument("--content", default="", help="分析摘要内容")
    p_daily.add_argument("--run-url", required=True, help="GitHub Runs URL")

    # buffett 子命令
    p_buffett = sub.add_parser("buffett", help="巴菲特筛选结果通知")
    p_buffett.add_argument("--result-file", required=True, help="筛选结果文件路径")
    p_buffett.add_argument("--ai-summary", default="", help="AI 核实摘要文本（Markdown）")

    # text 子命令
    p_text = sub.add_parser("text", help="通用文本通知")
    p_text.add_argument("--content", required=True, help="消息内容")
    p_text.add_argument("--title", default="通知", help="卡片标题")

    args = parser.parse_args()

    # 加载配置（直连 URL 模式下可跳过）
    config: dict | None = None
    if args.url is None:
        try:
            config = load_config(args.config)
        except (FileNotFoundError, ValueError) as e:
            print(f"❌ 配置加载失败: {e}", file=sys.stderr)
            sys.exit(1)

    if args.verbose:
        print(f"📋 使用配置: {args.config}", file=sys.stderr)
        print(f"📡 推送频道: {args.channel}", file=sys.stderr)

    # 构建 kwargs
    kwargs: dict = {"channel": args.channel}
    if args.mode in ("ci", "daily", "buffett", "text"):
        kwargs.update(vars(args))
        del kwargs["mode"]
        del kwargs["config"]
        del kwargs["channel"]
        del kwargs["verbose"]
        del kwargs["url"]
        del kwargs["secret"]

    # daily：未传 --top3 时自动从 results.json 补全
    if args.mode == "daily" and not kwargs.get("top3"):
        kwargs["top3"] = _read_top3_from_results()

    # 直连 URL 模式：绕过 send_notification 的配置加载，直接构建卡片 + send_feishu
    if args.url is not None:
        url, secret = _resolve_url_and_secret(config, args.channel, args.url, args.secret)
        if not url:
            print("❌ 未解析到飞书 Webhook URL", file=sys.stderr)
            sys.exit(1)
        if args.mode == "ci":
            payload = build_ci_card(
                status=args.status,
                branch=args.branch,
                commit=args.commit,
                jobs=args.jobs,
                run_url=args.run_url,
            )
        elif args.mode == "daily":
            payload = build_daily_card(
                status=args.status,
                date=args.date,
                tickers=args.tickers,
                top3=kwargs.get("top3", ""),
                run_url=args.run_url,
                content=args.content,
            )
        elif args.mode == "buffett":
            payload = build_buffett_card(args.result_file, ai_summary=args.ai_summary)
        else:  # text
            payload = build_text_card(content=args.content, title=args.title)
        ok = send_feishu(url, payload, secret=secret)
        sys.exit(0 if ok else 1)

    # 配置文件模式：走统一分发接口（此时 config 一定非 None，因直连 URL 模式已退出）
    assert config is not None
    ok = send_notification(mode=args.mode, config=config, **kwargs)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
