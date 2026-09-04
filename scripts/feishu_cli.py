#!/usr/bin/env python3
"""飞书推送 CLI 工具 — 支持多种通知模式。

使用方式：
  # CI 结果
  python scripts/feishu_cli.py ci \
    --status success \
    --branch master \
    --commit abc123 \
    --jobs 'lint✅ type-check✅ test✅' \
    --run-url "https://github.com/xxx/runs/123"

  # Daily 结果
  python scripts/feishu_cli.py daily \
    --status success \
    --date 2026-08-27 \
    --tickers "600519,000858" \
    --top3 "600519:BUY 0.85" \
    --run-url "https://github.com/xxx/runs/123"

  # 巴菲特筛选结果
  python scripts/feishu_cli.py buffett \
    --result-file outputs/results/buffett_screen_20260903.txt

  # 通用文本
  python scripts/feishu_cli.py text \
    --content "📊 今日分析完成，Top3: 600519 贵州茅台"

  # 指定频道
  python scripts/feishu_cli.py buffett \
    --result-file outputs/results/buffett_screen.txt \
    --channel alerts

  # 指定配置文件
  python scripts/feishu_cli.py buffett \
    --result-file outputs/results/buffett_screen.txt \
    --config ~/.config/feishu-notify/config.json
"""

from __future__ import annotations

import argparse
import sys

from feishu_core import load_config, send_notification


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
    p_ci.add_argument("--verbose", action="store_true", help="显示详细日志")

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

    # text 子命令
    p_text = sub.add_parser("text", help="通用文本通知")
    p_text.add_argument("--content", required=True, help="消息内容")
    p_text.add_argument("--title", default="通知", help="卡片标题")

    args = parser.parse_args()

    # 加载配置
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
    if args.mode == "ci":
        kwargs.update(vars(args))
        del kwargs["mode"]
        del kwargs["config"]
        del kwargs["channel"]
        del kwargs["verbose"]
    elif args.mode == "daily":
        kwargs.update(vars(args))
        del kwargs["mode"]
        del kwargs["config"]
        del kwargs["channel"]
        del kwargs["verbose"]
    elif args.mode == "buffett":
        kwargs.update(vars(args))
        del kwargs["mode"]
        del kwargs["config"]
        del kwargs["channel"]
        del kwargs["verbose"]
    elif args.mode == "text":
        kwargs.update(vars(args))
        del kwargs["mode"]
        del kwargs["config"]
        del kwargs["channel"]
        del kwargs["verbose"]

    # 发送通知
    ok = send_notification(mode=args.mode, config=config, **kwargs)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
