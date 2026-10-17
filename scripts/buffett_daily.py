#!/usr/bin/env python3
"""巴菲特六闸门筛选 + 逐只技术分析与飞书推送（统一版）。

完整流程：
  1. 运行同花顺巴菲特六闸门并行筛选（20并发）
  2. 写入结果文件 + 更新动态白名单
  3. 对每只通过股票逐一做技术分析
  4. 统一走 feishu_cli.py 推送：筛选汇总 → 逐只分析 → 最终摘要

命令行用法：
  # 一键运行筛选+分析+推送
  uv run python scripts/buffett_daily.py

  # 指定日期
  uv run python scripts/buffett_daily.py --date 2026-10-15

  # 跳过筛选（使用已有结果文件）
  uv run python scripts/buffett_daily.py --result-file outputs/results/buffett_screen_20261015.txt

  # 仅推送（不重新筛选，分析已有结果）
  uv run python scripts/buffett_daily.py --skip-screen

飞书推送说明：
  所有消息统一调用 scripts/feishu_cli.py，保证通知格式一致。
  子命令：buffett（汇总） / text（逐只） / daily（最终摘要）
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger

from scripts.daily_analysis import analyze_stock
from scripts.feishu_utils import push_feishu as _push_via_feishu_cli
from tests.buffett_cache import cache_clean, init_cache, load_all_cache, save_cache_to_db
from tests.buffett_screening import (
    _CONCURRENCY,
    AiVerificationResult,
    StockMetrics,
    batch_valuations,
    get_all_stocks,
    run_ai_verification,
    run_screening,
    write_result_file,
)
from trade_krono_cli.cli_commands._core_helpers import _load_env

# ─────────────────────────────────────────────────────────────────────────────
# 常量
# ─────────────────────────────────────────────────────────────────────────────

_RESULTS_DIR = Path("outputs/results")
_DYNAMIC_WHITELIST_PATH = _RESULTS_DIR / "buffett_dynamic_whitelist.txt"


# ─────────────────────────────────────────────────────────────────────────────
# 辅助函数
# ─────────────────────────────────────────────────────────────────────────────


def _write_dynamic_whitelist(results_pass: list[StockMetrics]) -> None:
    """将本次通过筛选的股票代码写入动态白名单文件。

    Parameters
    ----------
    results_pass : list[StockMetrics]
        通过五闸门的筛选结果列表
    """
    # 排除北交所股票（920 开头的 BJ 代码）
    codes = [
        r.ticker
        for r in results_pass
        if r.ticker and not r.ticker.upper().startswith(("920", "BJ."))
    ]
    _DYNAMIC_WHITELIST_PATH.write_text(",".join(codes), encoding="utf-8")
    logger.info(f"动态白名单已更新：{len(codes)} 只 → {_DYNAMIC_WHITELIST_PATH}")


def _save_ai_result(ai_result: AiVerificationResult, result_txt_path: str) -> None:
    """将 AI 核实结果写入 JSON 文件，并追加摘要到文本结果文件末尾。

    Parameters
    ----------
    ai_result : AiVerificationResult
        run_ai_verification 返回的分析结果
    result_txt_path : str
        原始文本结果文件路径，AI 摘要将追加在此文件末尾
    """
    results_dir = Path(result_txt_path).parent
    results_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = results_dir / f"buffett_screen_{date_str}_ai.json"

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(ai_result.__dict__, f, ensure_ascii=False, indent=2)
    logger.info(f"AI 核实结果已保存：{json_path}")

    summary = ai_result.full_summary_text.strip()
    if summary:
        with open(result_txt_path, "a", encoding="utf-8") as f:
            f.write(f"\n{'=' * 60}\n")
            f.write(f"🤖 AI 核实分析（agnes-3.0-flash）— {ai_result.date}\n")
            f.write(f"{'=' * 60}\n\n")
            f.write(summary)
            f.write("\n")
        logger.info(f"AI 摘要已追加至：{result_txt_path}")


def _ticker_to_code(ticker: str) -> str:
    """从带前缀的 ticker 中提取纯数字代码（如 sh.600519 → 600519）。"""
    if "." in ticker:
        return ticker.split(".", 1)[1]
    return ticker


def _normalize_ticker(ticker_code: str) -> str:
    """统一 ticker 前缀：sh. / sz. / bj.。

    Parameters
    ----------
    ticker_code : str
        股票代码，可带交易所后缀或纯数字

    Returns
    -------
    str
        规范化后的 ticker（含交易所前缀）
    """
    if "." in ticker_code:
        return ticker_code  # 已带前缀
    code = ticker_code
    if code.startswith("6"):
        return f"sh.{code}"
    if code.startswith("9"):
        return f"bj.{code}"
    return f"sz.{code}"


def _parse_passing_stocks(result_file: Path) -> list[tuple[str, str]]:
    """解析筛选结果文件，返回 [(raw_code, name), ...]。

    Parameters
    ----------
    result_file : Path
        筛选结果 .txt 文件路径

    Returns
    -------
    list[tuple[str, str]]
        通过筛选的股票列表（原始代码字符串, 名称）
    """
    if not result_file.exists():
        logger.warning(f"结果文件不存在: {result_file}")
        return []

    passing: list[tuple[str, str]] = []
    in_table = False
    separator_seen = False

    with open(result_file, encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if "通过五闸门" in stripped:
                continue
            if stripped.startswith("  ---") or "--------" in stripped:
                separator_seen = True
                in_table = True
                continue
            if in_table and separator_seen and stripped and not stripped.startswith("失败"):
                parts = stripped.split()
                if len(parts) >= 2:
                    raw_code = parts[0].split(".")[0]
                    name = parts[1]
                    if raw_code.isdigit() and name:
                        passing.append((raw_code, name))
            if "失败分布" in stripped:
                break

    return passing


def _parse_total_screened(result_file: Path) -> int:
    """从筛选结果文件中解析总样本数量。"""
    if not result_file.exists():
        return 0
    with open(result_file, encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if "通过五闸门" in stripped:
                m = re.search(r"共\s*(\d+)\s*只", stripped)
                if m:
                    return int(m.group(1))
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────────────────────


def run(
    buffett_result_file: str | None = None,
    date_str: str | None = None,
    skip_screen: bool = False,
) -> None:
    """执行巴菲特筛选 + 逐只技术分析与飞书推送。

    Parameters
    ----------
    buffett_result_file : str | None
        筛选结果文件路径（含日期后缀），为 None 时自动推断。
    date_str : str | None
        分析日期 YYYY-MM-DD，默认今天。
    skip_screen : bool
        是否跳过筛选步骤（直接使用已有结果文件）。
    """
    _load_env()
    today = date_str or datetime.now().strftime("%Y-%m-%d")
    date_compact = today.replace("-", "")
    result_file = str(_RESULTS_DIR / f"buffett_screen_{date_compact}.txt")

    logger.info(f"🎯 开始巴菲特筛选 + 技术面逐只分析  日期={today}")

    # 用于存放筛选结果，供 AI 核实复用
    results_pass: list[StockMetrics] = []
    results_fail: list[StockMetrics] = []

    # ── Step 1: 筛选 ───────────────────────────────────────────────────────
    if skip_screen and buffett_result_file:
        logger.info("📋 步骤 1/4：跳过筛选，使用指定结果文件...")
        result_file = buffett_result_file
    elif skip_screen:
        logger.info("📋 步骤 1/4：跳过筛选，使用今日结果文件...")
    else:
        # 运行完整筛选流程（含 AI 核实），复用结果供后续步骤
        logger.info("📋 步骤 1/4：运行巴菲特六闸门并行筛选...")
        results_pass, results_fail, skipped_no_val, total_filtered, elapsed = _run_full_screen(
            result_file, today
        )
        logger.info(f"✅ 巴菲特筛选完成（{elapsed:.1f}s），结果文件: {result_file}")

    # ── Step 2: 解析通过股票 + AI 核实 ──────────────────────────────────────
    passing = _parse_passing_stocks(Path(result_file))
    total_screened = _parse_total_screened(Path(result_file)) or 4966
    logger.info(f"📈 筛选通过 {len(passing)} 只股票（总样本 {total_screened} 只）")

    # 写入动态白名单
    if passing:
        pass_codes = [p[0] for p in passing]
        _write_dynamic_whitelist_from_codes(pass_codes)

    # AI 核实（从已运行的筛选结果中提取，无需重新筛选）
    logger.info("📋 步骤 2/4：AI 核实...")
    ai_summary = _run_ai_check(results_pass, results_fail, total_filtered, today)
    if ai_summary:
        logger.info(f"  AI 核实完成：{ai_summary}")
    else:
        logger.info("  AI 核实：未执行（LLM 未配置或调用失败）")

    # ── Step 3: 推送筛选汇总至飞书 ─────────────────────────────────────────
    logger.info("📋 步骤 3/4：推送飞书筛选汇总...")
    _push_via_feishu_cli(
        "buffett",
        result_file=result_file,
        ai_summary=ai_summary or "",
    )

    # ── Step 4: 逐只技术分析 + 实时推送 ────────────────────────────────────
    if not passing:
        logger.info("⚠️ 无通过股票，跳过逐只分析")
        return

    logger.info(f"📋 步骤 4/4：逐只技术分析（{len(passing)} 只）...")
    results: list[dict] = []
    ok_count = 0
    fail_count = 0

    for ticker_code, name in passing:
        ticker = _normalize_ticker(ticker_code)
        result = analyze_stock(ticker, end_date=today)
        result["name"] = name
        results.append(result)

        status_emoji = "✅" if result["status"] == "ok" else "⚠️"
        logger.info(
            f"  {status_emoji} {ticker} {name}: "
            f"分数={result['score']} 趋势={result['trend']} 均线={result['ma_signal']}"
        )

        if result["status"] == "ok":
            _push_stock_analysis(ticker_code, name, result, today)
            ok_count += 1
        else:
            fail_count += 1
            logger.warning(f"  ⚠️ {ticker} {name} 无数据，跳过推送")

    # ── Step 5: 最终汇总推送 ──────────────────────────────────────────────
    _push_final_summary(results, passing, today, ok_count, fail_count)


def _write_dynamic_whitelist_from_codes(codes: list[str]) -> None:
    """从代码列表写入动态白名单（简化版本，适用于解析结果文件场景）。

    Parameters
    ----------
    codes : list[str]
        通过筛选的股票代码列表（6位数字）
    """
    # 排除北交所股票（920 开头的 BJ 代码）
    codes = [c for c in codes if not c.upper().startswith(("920", "BJ."))]
    _DYNAMIC_WHITELIST_PATH.write_text(",".join(codes), encoding="utf-8")
    logger.info(f"动态白名单已更新：{len(codes)} 只 → {_DYNAMIC_WHITELIST_PATH}")


def _run_full_screen(
    result_file: str, date_str: str
) -> tuple[list[StockMetrics], list[StockMetrics], list[str], int, float]:
    """运行完整的巴菲特并行筛选流程（含 AI 核实）。

    Parameters
    ----------
    result_file : str
        结果文件保存路径
    date_str : str
        日期字符串 YYYY-MM-DD

    Returns
    -------
    tuple[list[StockMetrics], list[StockMetrics], list[str], int, float]
        (通过列表, 失败列表, 跳过股票代码列表, 过滤后样本数, 耗时秒数)
    """
    conn = init_cache()
    try:
        cleaned = cache_clean(conn)
        if cleaned > 0:
            logger.debug(f"缓存清理：清除 {cleaned} 条过期记录")

        cache = load_all_cache(conn)
        logger.info(
            f"缓存加载：估值 {len(cache.get('valuations', {}))}，财务 {len(cache.get('financials', {}))}"
        )

        # 1. 获取全量 A 股列表
        logger.info("  获取股票列表...")
        t0 = time.time()
        stocks = get_all_stocks(conn)
        elapsed_list = time.time() - t0
        logger.info(f"  获取 {len(stocks)} 只股票（{elapsed_list:.1f}s）")

        filtered: list[dict] = []
        for s in stocks:
            name = s.get("name", "")
            code = s.get("ticker", "")
            if any(kw in name for kw in ("ST", "*ST", "N", "C")):
                continue
            if code.startswith(("000", "399")):
                continue
            filtered.append(s)
        logger.info(f"  过滤后 {len(filtered)} 只（排除ST/次新）")

        # 2. 批量获取估值快照
        logger.info("  获取估值数据...")
        t0 = time.time()
        thscodes = [s["thscode"] for s in filtered]
        vals, val_new_entries = batch_valuations(thscodes, conn, cache)
        cache["valuations"].update(val_new_entries)
        elapsed_val = time.time() - t0
        hit = sum(1 for k in thscodes if f"val_{k}" in cache.get("valuations", {}))
        miss = len(thscodes) - hit
        logger.info(f"  获取 {len(vals)} 只估值（{elapsed_val:.1f}s，缓存命中 {hit}，请求 {miss}）")

        # 3. 并行筛选
        logger.info("  并行筛选...")
        t0 = time.time()
        results_pass, results_fail, skipped_no_val, elapsed_screen = run_screening(
            filtered, vals, conn, cache, concurrency=_CONCURRENCY
        )
        logger.info(
            f"  筛选完成：通过 {len(results_pass)} 只，失败 {len(results_fail)} 只，跳过 {skipped_no_val} 只"
        )

        # 4. 写入结果文件
        write_result_file(result_file, results_pass, results_fail)

        # 5. AI 核实
        logger.info("  AI 核实...")
        ai_result = run_ai_verification(results_pass, results_fail, len(filtered), date_str)
        _save_ai_result(ai_result, result_file)
        if ai_result.full_summary_text:
            logger.info(f"  AI 核实摘要：{ai_result.analysis_summary}")

        # 6. 保存缓存
        saved = save_cache_to_db(conn, cache)
        logger.info(f"  缓存已保存：{saved} 条新记录")

        return results_pass, results_fail, skipped_no_val, len(filtered), elapsed_screen
    finally:
        conn.close()


def _run_ai_check(
    results_pass: list[StockMetrics],
    results_fail: list[StockMetrics],
    total_screened: int,
    date_str: str,
) -> str:
    """基于已运行的筛选结果调用 AI 核实，返回摘要文本。

    Parameters
    ----------
    results_pass : list[StockMetrics]
        通过筛选的股票列表
    results_fail : list[StockMetrics]
        未通过筛选的股票列表
    total_screened : int
        总样本数量
    date_str : str
        日期字符串 YYYY-MM-DD

    Returns
    -------
    str
        AI 核实摘要文本，失败时返回空字符串
    """
    if not results_pass:
        return ""
    try:
        ai_result = run_ai_verification(results_pass, results_fail, total_screened, date_str)
        return ai_result.full_summary_text or ""
    except Exception as e:
        logger.opt(exception=True).warning(f"AI 核实失败: {e}")
        return ""


def _push_stock_analysis(
    ticker_code: str,
    name: str,
    result: dict,
    date_str: str,
) -> None:
    """推送单只股票技术分析结果至飞书。

    Parameters
    ----------
    ticker_code : str
        股票代码（6位数字）
    name : str
        股票名称
    result : dict
        analyze_stock 返回的分析结果
    date_str : str
        日期 YYYY-MM-DD
    """
    score = result.get("score", 0.0)
    emoji = "🟢" if score >= 65 else ("🟡" if score >= 45 else "🔴")
    action = "BUY" if score >= 65 else ("HOLD" if score >= 45 else "SELL")

    card = (
        f"**📊 {date_str} · {ticker_code} {name}**\n"
        f"🏆 巴菲特五闸门通过股\n"
        f"{emoji} **技术分：{score:.1f}**（{action}）\n"
        f"收盘价：{result.get('close', '?')}  今日涨跌：{result.get('change', 0):+.2f}%\n"
        f"趋势：{result['trend']}　均线信号：{result['ma_signal']}　量能信号：{result['vol_signal']}\n"
        f"MA5={result.get('ma5', '?')}　MA10={result.get('ma10', '?')}　MA20={result.get('ma20', '?')}"
    )

    _push_via_feishu_cli(
        "text",
        content=card,
        title=f"{ticker_code} {name} · 技术面分析",
    )


def _push_final_summary(
    results: list[dict],
    passing: list[tuple[str, str]],
    date_str: str,
    ok_count: int,
    fail_count: int,
) -> None:
    """推送最终汇总摘要至飞书。

    Parameters
    ----------
    results : list[dict]
        逐只技术分析结果列表
    passing : list[tuple[str, str]]
        筛选通过的股票 [(code, name), ...]
    date_str : str
        日期 YYYY-MM-DD
    ok_count : int
        有数据的股票数
    fail_count : int
        无数据的股票数
    """
    ok_results = [r for r in results if r["status"] == "ok"]
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
        f"**📊 {date_str} 巴菲特筛选股技术面汇总**\n",
        f"筛选通过 {ok_count} 只有数据 · BUY {len(buy_signals)} 只 · HOLD {len(hold_signals)} 只 · SELL {len(sell_signals)} 只"
        f"{'，' + str(fail_count) + ' 只无数据' if fail_count > 0 else ''}\n\n",
    ]
    for r in sorted(ok_results, key=lambda x: x["score"], reverse=True):
        emoji = "🔴" if r["score"] >= 65 else ("🟡" if r["score"] >= 45 else "⚪")
        summary_lines.append(
            f"• {emoji} **{r['ticker']} {r['name']}**  分={r['score']:.1f}  趋势={r['trend']}  "
            f"均线={r['ma_signal']}  量能={r['vol_signal']}  今日={r['change']:+.2f}%"
        )
    if fail_count > 0:
        no_data = [r for r in results if r["status"] != "ok"]
        summary_lines.append(f"\n⚠️ 无数据：{', '.join(r['ticker'] for r in no_data)}")

    content = "\n".join(summary_lines)

    _push_via_feishu_cli(
        "daily",
        status="success",
        date=date_str,
        tickers=",".join(p[0] for p in passing),
        top3=top3_str,
        run_url="",
        content=content,
    )


def main() -> None:
    """命令行入口。"""
    import argparse

    parser = argparse.ArgumentParser(
        description="巴菲特筛选 + 逐只技术分析与飞书推送（统一版）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--date", default=None, help="分析日期 YYYY-MM-DD（默认今天）")
    parser.add_argument(
        "--result-file",
        default=None,
        help="指定筛选结果文件路径（跳过筛选步骤）",
    )
    parser.add_argument(
        "--skip-screen",
        action="store_true",
        help="跳过筛选，直接使用已有结果文件",
    )
    args = parser.parse_args()

    run(
        buffett_result_file=args.result_file,
        date_str=args.date,
        skip_screen=args.skip_screen,
    )


if __name__ == "__main__":
    main()
