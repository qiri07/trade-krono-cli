#!/usr/bin/env python3
"""巴菲特六闸门筛选 + 逐只技术分析与飞书推送。

流程：
  1. 运行同花顺巴菲特六闸门并行筛选
  2. 推送筛选结果汇总至飞书
  3. 对每只通过股票逐一做技术分析，逐条推送飞书
  4. 推送最终汇总摘要
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger

from scripts.daily_analysis import analyze_stock
from scripts.feishu_core import load_config, send_notification

# ─────────────────────────────────────────────────────────────────────────────
# 常量
# ─────────────────────────────────────────────────────────────────────────────

_RESULTS_DIR = Path("outputs/results")
SCREEN_SCRIPT = Path("tests/buffett_screen_parallel.py")


def _ticker_to_code(ticker: str) -> str:
    """从带前缀的 ticker 中提取纯数字代码（如 sh.600519 → 600519）。"""
    if "." in ticker:
        return ticker.split(".", 1)[1]
    return ticker


def _parse_total_screened(result_file: Path) -> int:
    """从筛选结果文件中解析总样本数量。"""
    if not result_file.exists():
        return 0
    with open(result_file, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if "通过五闸门" in stripped:
                m = re.search(r"共\s*(\d+)\s*只", stripped)
                if m:
                    return int(m.group(1))
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# 辅助函数
# ─────────────────────────────────────────────────────────────────────────────


def _parse_passing_stocks(result_file: Path) -> list[tuple[str, str]]:
    """解析筛选结果文件，返回 [(ticker_code, name), ...] 列表。

    结果文件格式示例：
      巴菲特六闸门筛选结果（增强版）— 2026-09-18 11:00
      通过五闸门（①~⑤）的股票共 N 只

        代码      名称      PE_TTM      PB    ROE%  毛利率%  负债率%   CAGR%   稳定性  现金流质量 数据备注
        -------- ---------- ------- ------ ------- ------- ------- ------- -------- ---------- ----------
        600519   贵州茅台    28.5     9.12   30.2    91.3    10.5    12.3   优秀     优         -
        ...
    """
    if not result_file.exists():
        logger.warning(f"结果文件不存在: {result_file}")
        return []

    passing: list[tuple[str, str]] = []
    in_table = False
    separator_seen = False

    with open(result_file, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if "通过五闸门" in stripped:
                # 找到表头行
                continue
            if stripped.startswith("  ---") or "--------" in stripped:
                separator_seen = True
                in_table = True
                continue
            if in_table and separator_seen and stripped and not stripped.startswith("失败"):
                parts = stripped.split()
                if len(parts) >= 2:
                    raw_code = parts[0]
                    # 去掉 .BJ / .SH / .SZ 后缀后再校验
                    code = raw_code.split(".")[0]
                    name = parts[1]
                    if code.isdigit() and name and len(name) > 0:
                        passing.append((raw_code, name))
            # 在"失败分布"处停止
            if "失败分布" in stripped:
                break

    return passing


def _build_buffett_card(passing: list[tuple[str, str]], total_screened: int, date_str: str) -> str:
    """构建巴菲特筛选结果飞书消息文本。"""
    lines = [
        f"**📊 {date_str} 巴菲特六闸门筛选结果**\n",
        f"总样本：{total_screened} 只 → 通过 **{len(passing)}** 只\n\n",
    ]
    if not passing:
        lines.append("⚠️ 本次无股票通过五闸门筛选\n")
    else:
        lines.append("**✅ 通过筛选（五闸门①~⑤）**\n")
        for code, name in passing:
            lines.append(f"• `{code}` {name}")
        lines.append("\n")
    return "\n".join(lines)


def _build_stock_analysis_card(
    ticker_code: str,
    name: str,
    result: dict,
    date_str: str,
) -> str:
    """构建单只巴菲特筛选股的技术分析飞书卡片。"""
    emoji = "🟢" if result["score"] >= 65 else ("🟡" if result["score"] >= 45 else "🔴")
    action = "BUY" if result["score"] >= 65 else ("HOLD" if result["score"] >= 45 else "SELL")
    return (
        f"**📊 {date_str} · {ticker_code} {name}**\n"
        f"🏆 巴菲特五闸门通过股\n"
        f"{emoji} **技术分：{result['score']}**（{action}）\n"
        f"收盘价：{result.get('close', '?')}  今日涨跌：{result.get('change', 0):+.2f}%\n"
        f"趋势：{result['trend']}　均线信号：{result['ma_signal']}　量能信号：{result['vol_signal']}\n"
        f"MA5={result.get('ma5', '?')}　MA10={result.get('ma10', '?')}　MA20={result.get('ma20', '?')}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────────────────────


def run(buffett_result_file: str | None = None, date_str: str | None = None) -> None:
    """执行巴菲特筛选 + 逐只技术分析 + 飞书推送。

    Parameters
    ----------
    buffett_result_file : str | None
        筛选结果文件路径（含日期后缀），由筛选脚本自动生成。
        为 None 时自动推断为 outputs/results/buffett_screen_<date>.txt
    date_str : str | None
        分析日期 YYYY-MM-DD，默认今天
    """
    from trade_krono_cli.cli_commands._core_helpers import _load_env

    _load_env()
    today = date_str or datetime.now().strftime("%Y-%m-%d")
    # 筛选脚本使用 YYYYMMDD 格式命名结果文件
    date_compact = today.replace("-", "")
    result_file = buffett_result_file or str(_RESULTS_DIR / f"buffett_screen_{date_compact}.txt")

    logger.info(f"🎯 开始巴菲特筛选 + 技术面逐只分析  日期={today}")

    # ── Step 1: 运行巴菲特并行筛选（仅当未指定结果文件时执行）─────────────
    if buffett_result_file:
        logger.info("📋 步骤 1/3：使用指定结果文件，跳过筛选...")
    else:
        logger.info("📋 步骤 1/3：运行巴菲特六闸门筛选...")
        t0 = time.time()
        env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parent.parent)}
        proc = subprocess.run(
            [sys.executable, str(SCREEN_SCRIPT)],
            capture_output=True,
            text=True,
            timeout=3600,  # 最多等 1 小时
            env=env,
        )
    elapsed = time.time() - t0 if not buffett_result_file else 0
    # 输出 stderr + stdout 便于排查
    if not buffett_result_file:
        if proc.stdout:
            for line in proc.stdout.splitlines():
                logger.info(f"  [筛选] {line}")
        if proc.stderr:
            for line in proc.stderr.splitlines():
                logger.warning(f"  [筛选err] {line}")

        if proc.returncode != 0:
            logger.error(
                f"❌ 巴菲特筛选执行失败，returncode={proc.returncode}（耗时 {elapsed:.1f}s）"
            )
            logger.error("跳过后续步骤")
            return

        logger.info(f"✅ 巴菲特筛选完成（{elapsed:.1f}s），结果文件: {result_file}")

    # ── Step 2: 解析通过股票 + 推送汇总 ──────────────────────────────────
    passing = _parse_passing_stocks(Path(result_file))
    logger.info(f"📈 筛选通过 {len(passing)} 只股票")
    total_screened = _parse_total_screened(Path(result_file)) or 4966

    config = load_config()

    # 2a: 推送筛选汇总
    summary_card = _build_buffett_card(passing, total_screened=total_screened, date_str=today)
    ok = send_notification(
        mode="text",
        config=config,
        content=summary_card,
        title=f"巴菲特筛选 {today}",
    )
    logger.info(f"{'✅' if ok else '❌'} 筛选汇总已推送飞书")

    # ── Step 3: 逐只技术分析 + 实时推送 ──────────────────────────────────
    if not passing:
        logger.info("⚠️ 无通过股票，跳过逐只分析")
        return

    results: list[dict] = []
    for ticker_code, name in passing:
        # 统一 ticker 前缀：sh. / sz. / bj.
        if "." in ticker_code:
            ticker = ticker_code  # 已带前缀（如 bj.920208）
        elif ticker_code.startswith("6"):
            ticker = f"sh.{ticker_code}"
        elif ticker_code.startswith("9"):
            ticker = f"bj.{ticker_code}"
        else:
            ticker = f"sz.{ticker_code}"

        result = analyze_stock(ticker, end_date=today)
        result["name"] = name  # 用筛选返回的名称覆盖
        results.append(result)

        status_emoji = "✅" if result["status"] == "ok" else "⚠️"
        logger.info(
            f"  {status_emoji} {ticker} {name}: "
            f"分数={result['score']} 趋势={result['trend']} 均线={result['ma_signal']}"
        )

        if result["status"] == "ok":
            card = _build_stock_analysis_card(ticker_code, name, result, today)
            ok = send_notification(
                mode="text",
                config=config,
                content=card,
                title=f"{ticker_code} {name} — 技术面分析",
            )
            logger.info(f"  {'✅' if ok else '❌'} 已推送飞书: {ticker_code} {name}")
        else:
            logger.warning(f"  ⚠️ {ticker} {name} 无数据，跳过推送")

    # ── Step 4: 最终汇总推送 ──────────────────────────────────────────────
    ok_results = [r for r in results if r["status"] == "ok"]
    no_data_results = [r for r in results if r["status"] != "ok"]
    buy_signals = [r for r in ok_results if r["score"] >= 65]
    hold_signals = [r for r in ok_results if 45 <= r["score"] < 65]
    sell_signals = [r for r in ok_results if r["score"] < 45]

    top3_sorted = sorted(ok_results, key=lambda x: x["score"], reverse=True)[:3]
    top3_str = (
        "  ".join(
            f"{_ticker_to_code(r['ticker'])}.{r['name']}:{'BUY' if r['score'] >= 65 else 'HOLD'} {r['score']}分"
            for r in top3_sorted
        )
        or "无"
    )

    summary_lines = [
        f"**📊 {today} 巴菲特筛选股技术面汇总**\n",
        f"筛选通过 {len(ok_results)} 只有数据 · BUY {len(buy_signals)} 只 · HOLD {len(hold_signals)} 只 · SELL {len(sell_signals)} 只"
        f"{'，' + str(len(no_data_results)) + ' 只无数据' if no_data_results else ''}\n\n",
    ]
    for r in sorted(ok_results, key=lambda x: x["score"], reverse=True):
        emoji = "🔴" if r["score"] >= 65 else ("🟡" if r["score"] >= 45 else "⚪")
        summary_lines.append(
            f"• {emoji} **{r['ticker']} {r['name']}**  分={r['score']}  趋势={r['trend']}  "
            f"均线={r['ma_signal']}  量能={r['vol_signal']}  今日={r['change']:+.2f}%"
        )
    if no_data_results:
        summary_lines.append(f"\n⚠️ 无数据：{', '.join(r['ticker'] for r in no_data_results)}")
    summary_content = "\n".join(summary_lines)

    ok = send_notification(
        mode="daily",
        config=config,
        status="success",
        date=today,
        tickers=", ".join(t[0] for t in passing),
        top3=top3_str,
        run_url="",
        content=summary_content,
    )
    if ok:
        logger.info("✅ 最终汇总飞书推送成功")
    else:
        logger.error("❌ 最终汇总飞书推送失败")


def main() -> None:
    """命令行入口。"""
    import argparse

    parser = argparse.ArgumentParser(
        description="巴菲特筛选 + 逐只技术分析与飞书推送",
    )
    parser.add_argument("--date", default=None, help="分析日期 YYYY-MM-DD（默认今天）")
    parser.add_argument("--result-file", default=None, help="指定筛选结果文件路径")
    args = parser.parse_args()

    run(buffett_result_file=args.result_file, date_str=args.date)


if __name__ == "__main__":
    main()
