#!/usr/bin/env python3
"""白名单股票 TradingAgents 分析 + 逐只飞书推送。

基于内置 TradingAgents-astock 多 Agent 深度分析，
每只股票分析完成后立即推送飞书，最后输出汇总。

用法：
  # 分析全部白名单（静态+动态）
  uv run python scripts/daily_ta_push.py

  # 指定日期和股票
  uv run python scripts/daily_ta_push.py --tickers "600519,000858"

  # 仅白名单中的A股（剔除港股/北交所）
  uv run python scripts/daily_ta_push.py --a-share-only
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger

from scripts._utils import STOCK_NAMES, normalize_ticker_key
from scripts.feishu_utils import push_feishu
from trade_krono_cli.cli_commands._core_helpers import _load_env
from trade_krono_cli.pipeline import QuantPipeline
from trade_krono_cli.pipeline_config import PipelineConfig

# ── 路径常量 ─────────────────────────────────────────────────────────────────
_WHITELIST_STATIC = Path("outputs/cache/known_tickers.txt")
_WHITELIST_DYNAMIC = Path("outputs/results/buffett_dynamic_whitelist.txt")


def _load_whitelist(a_share_only: bool = False) -> list[str]:
    """加载白名单股票（SYNC_WHITELIST + 动态巴菲特筛选），返回带交易所前缀的列表。"""
    from trade_krono_cli.config import get_settings

    settings = get_settings()
    # 主白名单：DAILY_ANALYSIS_WHITELIST（逗号分隔的6位代码）
    raw_list = settings.daily_analysis_whitelist.strip()
    if not raw_list:
        # fallback 到 SYNC_WHITELIST
        raw_list = settings.sync_whitelist.strip()

    static_codes: set[str] = set()
    for code in raw_list.split(","):
        code = code.strip()
        if code:
            static_codes.add(code)

    dynamic_codes: set[str] = set()
    if _WHITELIST_DYNAMIC.exists():
        dynamic_codes.update(
            c.strip()
            for c in _WHITELIST_DYNAMIC.read_text(encoding="utf-8").split(",")
            if c.strip()
        )

    merged: list[str] = []
    seen: set[str] = set()
    for code in list(static_codes) + list(dynamic_codes):
        if code in seen:
            continue
        # 规范化格式
        normalized = normalize_ticker_key(code)
        # 剔除北交所（92开头）
        if a_share_only and normalized.startswith("bj."):
            continue
        if a_share_only and normalized.startswith("hk."):
            continue
        seen.add(normalized)
        merged.append(normalized)

    logger.info(f"📋 白名单加载完成: {len(merged)} 只")
    return merged


def _get_stock_name(ticker: str) -> str:
    """获取股票名称。"""
    code = ticker.split(".", 1)[1] if "." in ticker else ticker
    return STOCK_NAMES.get(code, "")


def _build_feishu_card(result: Any, date: str) -> str:
    """根据 TA 分析结果构建飞书消息文本。"""
    ticker = result.ticker
    name = _get_stock_name(ticker)

    # 优先用 investment_decision，fallback 到 legacy 字段
    if result.investment_decision is not None:
        dec = result.investment_decision
        signal = dec.signal.value if hasattr(dec.signal, "value") else str(dec.signal)
        confidence = dec.confidence
        reasoning = dec.thesis or ""
    else:
        signal = result.signal or "HOLD"
        confidence = result.confidence or 0.0
        reasoning = result.reasoning or ""

    emoji_map = {"BUY": "🟢", "OVERWEIGHT": "🟢", "HOLD": "🟡", "SELL": "🔴"}
    emoji = emoji_map.get(signal.upper(), "⚪")

    lines: list[str] = [
        f"**📊 {date} · {ticker} {name}**",
        f"{emoji} **信号：{signal}** | 置信度：{confidence:.0f}%",
    ]

    if reasoning:
        short_reason = reasoning[:600] + ("…" if len(reasoning) > 600 else "")
        lines.append(f"\n**核心推理：**\n{short_reason}")

    return "\n".join(lines)


def main() -> None:
    """主入口：TA 分析 → 逐只飞书推送。"""
    import argparse

    parser = argparse.ArgumentParser(description="TradingAgents 白名单分析 + 飞书推送")
    parser.add_argument("--date", default=time.strftime("%Y-%m-%d"), help="分析日期")
    parser.add_argument(
        "--tickers",
        default=None,
        help="股票代码（逗号分隔，不带交易所前缀）",
    )
    parser.add_argument(
        "--a-share-only",
        action="store_true",
        help="仅分析 A 股（剔除港股、北交所）",
    )
    args = parser.parse_args()

    _load_env()

    # ── 解析股票列表 ───────────────────────────────────────────────────────
    if args.tickers:
        raw_codes = [c.strip() for c in args.tickers.split(",") if c.strip()]
        tickers: list[str] = [normalize_ticker_key(c) for c in raw_codes]
    else:
        tickers = _load_whitelist(a_share_only=args.a_share_only)

    if not tickers:
        logger.error("❌ 股票列表为空")
        sys.exit(1)

    logger.info(f"🚀 开始 TradingAgents 分析 | {len(tickers)} 只 | date={args.date}")

    # ── 运行 TA 分析 ───────────────────────────────────────────────────────
    pipeline = QuantPipeline(config=PipelineConfig.default())
    results = pipeline.run_ta_only(tickers=tickers, date=args.date)

    # ── 逐只推送飞书（统一走 feishu_cli.py）────────────────────────────
    success_count = 0
    fail_count = 0

    for i, result in enumerate(results, 1):
        ticker = result.ticker
        name = _get_stock_name(ticker)

        if result.error:
            logger.warning(f"  ⚠️ [{i}/{len(results)}] {ticker} {name}: 分析失败 — {result.error}")
            fail_count += 1
            continue

        card = _build_feishu_card(result, args.date)

        ok = push_feishu(
            "text",
            content=card,
            title=f"{ticker} {name} · {result.signal or 'HOLD'}",
        )
        if ok:
            confidence = (
                result.investment_decision.confidence
                if result.investment_decision is not None
                else (result.confidence or 0.0)
            )
            logger.info(
                f"  ✅ [{i}/{len(results)}] {ticker} {name}: "
                f"{result.signal or 'HOLD'}({confidence:.0f}%) → 已推送飞书"
            )
            success_count += 1
        else:
            logger.error(f"  ❌ [{i}/{len(results)}] {ticker} {name}: 飞书推送失败")
            fail_count += 1

    # ── 汇总 ───────────────────────────────────────────────────────────────
    logger.info(
        f"📊 分析完成: {success_count} 只成功推送, {fail_count} 只失败, 共 {len(results)} 只"
    )


if __name__ == "__main__":
    main()
