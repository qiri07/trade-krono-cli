#!/usr/bin/env python3
"""全市场扫描多头排列股票 + 分析 + 飞书推送。

用法：
  uv run python scripts/multi_scan.py
  uv run python scripts/multi_scan.py --top 50
  uv run python scripts/multi_scan.py --date 2026-09-24
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
from loguru import logger

from scripts.feishu_core import load_config, send_notification
from scripts.daily_analysis import analyze_stock, _get_stock_name, _load_env
from trade_krono_cli.cache import get_cache
from trade_krono_cli.config import get_settings

# ── 路径常量 ────────────────────────────────────────────────────────────────

WORK_DIR = Path("/run/media/onai/MyDisk/Work")
RESULTS_DIR = WORK_DIR / "trade-krono-cli" / "outputs" / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


def _get_cache_db_path() -> Path:
    """获取 pipeline_cache.db 路径（兼容测试隔离）"""
    try:
        settings = get_settings()
        return Path(settings.cache_dir) / "pipeline_cache.db"
    except Exception:
        return Path("outputs/cache/pipeline_cache.db")


def _get_all_tickers(db_path: Path) -> list[str]:
    """从缓存数据库获取所有股票代码（排除北交所）"""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT ticker FROM kline_cache ORDER BY ticker")
    all_tickers = [r[0] for r in cur.fetchall()]
    conn.close()

    # 排除北交所（bj.开头）
    astock_tickers = [t for t in all_tickers if not t.startswith("bj.")]
    logger.info(f"📊 全市场扫描：共 {len(all_tickers)} 只，排除北交所后 {len(astock_tickers)} 只")
    return astock_tickers


def scan_multi_head_stocks(
    tickers: list[str], max_workers: int = 20, top_n: int | None = None
) -> list[dict[str, Any]]:
    """扫描全市场，找出多头排列的股票。

    Args:
        tickers: 股票代码列表（带 sh./sz. 前缀）
        max_workers: 最大并发数
        top_n: 只返回分数最高的 N 只（None 表示全部）

    Returns:
        按分数降序排列的股票分析结果列表
    """
    results: list[dict[str, Any]] = []
    total = len(tickers)
    logger.info(f"🔍 开始扫描 {total} 只股票，最大并发 {max_workers}...")

    # 批量分析
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_ticker = {
            executor.submit(analyze_stock, ticker): ticker
            for ticker in tickers
        }

        completed = 0
        for future in as_completed(future_to_ticker):
            ticker = future_to_ticker[future]
            completed += 1
            if completed % 500 == 0:
                logger.info(f"  进度: {completed}/{total} ({completed*100//total}%)")

            try:
                result = future.result()
                if result.get("status") == "ok":
                    # 筛选多头排列且分数>=50的股票
                    if result.get("ma_signal") == "多头排列✅" and result.get("score", 0) >= 50:
                        results.append(result)
            except Exception as e:
                logger.warning(f"  ⚠️ {ticker} 分析失败: {e}")

    # 按分数降序排序
    results.sort(key=lambda x: x.get("score", 0), reverse=True)

    if top_n:
        results = results[:top_n]

    logger.info(f"✅ 扫描完成：找到 {len(results)} 只多头排列股票（分数≥50）")
    return results


def _build_summary_card(results: list[dict[str, Any]], date_str: str) -> str:
    """构建汇总消息卡片"""
    if not results:
        return f"📊 {date_str} 全市场扫描结果\n\n未找到多头排列且分数≥50的股票"

    lines = [f"📊 **{date_str} 全市场多头排列扫描**\n", f"共找到 **{len(results)}** 只符合条件"]
    lines.append("")

    # 分类统计
    buy_stocks = [r for r in results if r.get("score", 0) >= 65]
    hold_stocks = [r for r in results if 50 <= r.get("score", 0) < 65]

    if buy_stocks:
        lines.append(f"🟢 **买入关注**（{len(buy_stocks)}只）")
        for r in buy_stocks[:10]:
            lines.append(
                f"  • {r['ticker']} {r['name']}: {r['score']}分 "
                f"{r['trend']} {r['vol_signal']}"
            )
        lines.append("")

    if hold_stocks:
        lines.append(f"🟡 **观望等待**（{len(hold_stocks)}只）")
        for r in hold_stocks[:10]:
            lines.append(
                f"  • {r['ticker']} {r['name']}: {r['score']}分 "
                f"{r['trend']} {r['vol_signal']}"
            )
        lines.append("")

    # Top 5 详情
    lines.append("---")
    lines.append("📈 **Top 5 详情**")
    for i, r in enumerate(results[:5], 1):
        lines.append(f"\n**{i}. {r['ticker']} {r['name']}** ({r['score']}分)")
        lines.append(f"   收盘价: {r.get('close', '?')}  今日涨跌: {r.get('change', 0):+.2f}%")
        lines.append(f"   趋势: {r['trend']}  均线: {r['ma_signal']}  量能: {r['vol_signal']}")
        lines.append(
            f"   MA5={r.get('ma5', '?')} MA10={r.get('ma10', '?')} MA20={r.get('ma20', '?')}"
        )
        buy_points = r.get("buy_points", [])
        if buy_points:
            lines.append(f"   买点: {'、'.join(buy_points[:3])}")
        sell_points = r.get("sell_points", [])
        if sell_points:
            lines.append(f"   卖点: {'、'.join(sell_points[:3])}")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="全市场扫描多头排列股票")
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"), help="分析日期")
    parser.add_argument("--top", type=int, default=None, help="只返回前N只（默认全部）")
    parser.add_argument("--workers", type=int, default=20, help="并发数（默认20）")
    args = parser.parse_args()

    _load_env()

    # 获取数据库路径
    db_path = _get_cache_db_path()
    if not db_path.exists():
        logger.error(f"❌ 缓存数据库不存在: {db_path}")
        sys.exit(1)

    # 获取所有A股代码
    tickers = _get_all_tickers(db_path)

    # 扫描多头排列股票
    results = scan_multi_head_stocks(tickers, max_workers=args.workers, top_n=args.top)

    # 保存结果
    date_str = args.date.replace("-", "")
    result_file = RESULTS_DIR / f"multi_head_{date_str}.txt"
    result_file.write_text(
        _build_summary_card(results, args.date), encoding="utf-8"
    )
    logger.info(f"💾 结果已保存: {result_file}")

    # 飞书推送
    if results:
        config = load_config()
        summary = _build_summary_card(results, args.date)

        # 逐只推送详情
        for r in results[:20]:  # 最多推送20只
            card = (
                f"📊 **{args.date} · {r['ticker']} {r['name']}**\n"
                f"🟢 **综合分：{r['score']}**\n"
                f"收盘价：{r.get('close', '?')}  今日涨跌：{r.get('change', 0):+.2f}%\n"
                f"趋势：{r['trend']}　均线信号：{r['ma_signal']}　量能信号：{r['vol_signal']}\n"
                f"MA5={r.get('ma5', '?')}　MA10={r.get('ma10', '?')}　MA20={r.get('ma20', '?')}\n"
            )
            buy_points = r.get("buy_points", [])
            if buy_points:
                card += f"🟢 买点信号：{'、'.join(buy_points[:3])}\n"
            ok = send_notification(mode="text", config=config, content=card, title=f"{r['ticker']} {r['name']}")
            logger.info(f"  {'✅' if ok else '❌'} 已推送飞书: {r['ticker']} {r['name']}")

        # 汇总推送
        ok = send_notification(
            mode="text",
            config=config,
            content=summary,
            title=f"📊 全市场多头排列扫描 {args.date}",
        )
        logger.info(f"{'✅' if ok else '❌'} 汇总飞书推送完成")
    else:
        logger.warning("⚠️ 未找到符合条件的股票")


if __name__ == "__main__":
    main()
